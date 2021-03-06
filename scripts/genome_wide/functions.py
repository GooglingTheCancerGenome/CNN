import gzip
import json
import logging
import os
from itertools import groupby
from statistics import mean, stdev

import numpy as np
import pandas as pd
import pysam
import twobitreader as twobit
from cigar import Cigar

# import matplotlib.pyplot as plt

del_min_size = 50
ins_min_size = 50

'''
Generic functions used in the channel scripts
'''


def is_left_clipped(read):
    '''
    :param read: read object of the class pysam.AlignedSegment
    :return: True if the read is soft (4) or hard (5) clipped on the left, False otherwise
    '''
    if read.cigartuples is not None:
        if read.cigartuples[0][0] in [4, 5]:
            return True
    return False


def is_right_clipped(read):
    '''
    :param read: read object of the class pysam.AlignedSegment
    :return: True if the read is soft (4) or hard (5) clipped on the right, False otherwise
    '''
    if read.cigartuples is not None:
        if read.cigartuples[-1][0] in [4, 5]:
            return True
    return False


def is_clipped(read):
    '''
    :param read: read object of the class pysam.AlignedSegment
    :return: True if the read is soft (4) or hard (5) clipped on the left or on the right, False otherwise
    '''
    if read.cigartuples is not None:
        if is_left_clipped(read) or is_right_clipped(read):
            return True
    return False


def has_suppl_aln(read):
    return read.has_tag('SA')


def get_suppl_aln(read):
    '''
    This function returns the chromosome and start position of the first supplementary alignment ('SA' tag) for a read.
    :param read: read object of the class pysam.AlignedSegment
    :return: a tuple with chromosome and start position of the first supplementary alignment. None if there are no
    supplementary alignments.
    '''
    def query_len(cigar_string):
        """
        Given a CIGAR string, return the number of bases consumed from the
        query sequence.
        """
        read_consuming_ops = ("M", "D", "N", "=", "X")
        result = 0
        cig_iter = groupby(cigar_string, lambda chr: chr.isdigit())
        for _, length_digits in cig_iter:
            length = int(''.join(length_digits))
            op = next(next(cig_iter)[1])
            if op in read_consuming_ops:
                result += length
        return result

    if len(read.get_tag('SA')) > 0:
        # get first supplemental alignment
        supp_aln = read.get_tag('SA').split(';')[0]
        sa_info = supp_aln.split(',')
        chr_sa = sa_info[0]
        start_sa = int(sa_info[1])
        strand_sa = sa_info[2]
        cigar_sa = sa_info[3]
        mapq_sa = sa_info[4]

        if int(mapq_sa) < 10:
            return None
        start_sa -= 1
        return chr_sa, start_sa, strand_sa, cigar_sa
    return None


# Return start and end position of deletions and insertions
def get_indels(read):
    dels_start = []
    dels_end = []
    ins = []
    pos = read.reference_start
    if read.cigarstring is not None:
        cigar = Cigar(read.cigarstring)
        cigar_list = list(cigar.items())
        # print('{}:{}'.format(read.reference_name, read.reference_start))
        # print(cigar_list)
        for ct in cigar_list:
            # D is 2, I is 1
            if ct[1] == 'D' and ct[0] >= del_min_size:
                # dels.append(('D', pos, pos+ct[0]))
                dels_start.append(pos + 1)
                dels_end.append(pos + ct[0])
                # print('small DEL at pos {}:{}-{}'.format(read.reference_name, pos + 1, pos + ct[0]))
                # print(cigar_list)
            elif ct[1] == 'I' and ct[0] >= ins_min_size:
                # ins.append(('I', pos, pos+ct[0]))
                # print('small INS at pos {}:{}'.format(read.reference_name, pos))
                ins.append(pos)
            elif ct[1] in ['M', '=', 'X', 'D']:
                pos = pos + ct[0]

    return dels_start, dels_end, ins


def has_indels(read):
    if read.cigartuples is not None:
        cigar_set = {ct[0] for ct in read.cigartuples}
        # D is 2, I is 1
        if len(set([1, 2]) & cigar_set) > 0:
            return True
        return False
    return False


# Return the mate of a read. Get read mate from BAM file
def get_read_mate(read, bamfile):
    '''
    This function was used in a previous version of the code when we needed to check if the mate was clipped.
    Retrieving the mate for each read is time consuming and should be avoided when not strictly necessary.

    :param read: object of the class pysam.AlignedSegment whose mate needs to be retrieved
    :param bamfile: BAM file containing both the read and its mate
    :return: mate, object of the class pysam.AlignedSegment, if a mate for the read is found. Return None otherwise.
    '''
    # The mate is located at:
    # chromosome: read.next_reference_name
    # positions: [read.next_reference_start, read.next_reference_start+1]
    # Fetch all the reads in that location and retrieve the mate
    for mate in bamfile.fetch(read.next_reference_name,
                              read.next_reference_start,
                              read.next_reference_start + 1,
                              multiple_iterators=True):
        # A read and its mate have the same query_name
        if mate.query_name == read.query_name:
            # Check if read is first in pair (read1) and mate is second in pair (read2) or viceversa
            if (read.is_read1 and mate.is_read2) or (read.is_read2
                                                     and mate.is_read1):
                # print('Mate is: ' + str(mate))
                return mate
    return None


def get_reference_sequence(HPC_MODE, REF_GENOME):
    if HPC_MODE:
        # Path on the HPC of the 2bit version of the human reference genome
        genome = twobit.TwoBitFile(
            os.path.join(
                '/hpc/cog_bioinf/ridder/users/lsantuari/Datasets/genomes',
                REF_GENOME + '.2bit'))
    else:
        # Path on the local machine of the 2bit version of the human reference genome
        genome = twobit.TwoBitFile(
            os.path.join('/Users/lsantuari/Documents/Data/GiaB/reference',
                         REF_GENOME + '.2bit'))

    return genome


def is_flanked_by_n(chrname, pos, HPC_MODE, REF_GENOME):
    genome = get_reference_sequence(HPC_MODE, REF_GENOME)
    if "N" in genome['chr' + chrname][pos - 1:pos + 1].upper():
        return True
    return False


# Return a one-hot encoding for the chromosome region chr:start-stop
# with Ns encoded as 1 and other chromosomes encoded as 0
def get_one_hot_sequence(chrname, start, stop, nuc, HPC_MODE, REF_GENOME):
    genome = get_reference_sequence(HPC_MODE, REF_GENOME)
    if chrname == 'MT':
        chrname = 'M'
    chrname = chrname if REF_GENOME == 'GRCh38' else 'chr' + chrname

    return np.array([
        1 if x.lower() == nuc.lower() else 0
        for x in genome[chrname][start:stop]], dtype=np.uint8)


def get_one_hot_sequence_by_list(twobitfile, chrname, positions):
    genome = twobit.TwoBitFile(twobitfile)
    whole_chrom = str(genome[chrname])
    nuc_list = ['A', 'T', 'C', 'G', 'N']
    res = np.zeros(shape=(len(positions), len(nuc_list)), dtype=np.uint32)
    for i, nuc in enumerate(nuc_list, start=0):
        res[:, i] = np.array([
            1 if whole_chrom[pos].lower() == nuc.lower() else 0
            for pos in positions
        ])
    return res


# From https://github.com/joferkington/oost_paper_code/blob/master/utilities.py
def is_outlier(points, thresh=3.5):
    """
    Returns a boolean array with True if points are outliers and False
    otherwise.
    Parameters:
    -----------
        points : An numobservations by numdimensions array of observations
        thresh : The modified z-score to use as a threshold. Observations with
            a modified z-score (based on the median absolute deviation) greater
            than this value will be classified as outliers.
    Returns:
    --------
        mask : A numobservations-length boolean array.
    References:
    ----------
        Boris Iglewicz and David Hoaglin (1993), "Volume 16: How to Detect and
        Handle Outliers", The ASQC Basic References in Quality Control:
        Statistical Techniques, Edward F. Mykytka, Ph.D., Editor.
    """
    if len(points.shape) == 1:
        points = points[:, None]
    median = np.median(points, axis=0)
    diff = np.sum((points - median)**2, axis=-1)
    diff = np.sqrt(diff)
    med_abs_deviation = np.median(diff)

    modified_z_score = 0.6745 * diff / med_abs_deviation

    return modified_z_score > thresh


def get_config_file():
    with open(os.path.join(os.path.dirname(__file__), 'parameters.json'),
              'r') as f:
        config = json.load(f)
    return config


def get_chr_list():

    chrlist = [str(c) for c in list(np.arange(1, 23))]
    chrlist.extend(['X', 'Y'])

    return chrlist


def get_chr_len_dict(ibam):
    chr_list = get_chr_list()
    # check if the BAM file exists
    assert os.path.isfile(ibam)
    # open the BAM file
    bamfile = pysam.AlignmentFile(ibam, "rb")

    # Extract chromosome length from the BAM header
    header_dict = bamfile.header
    chr_dict = {i['SN']: i['LN'] for i in header_dict['SQ']}
    chr_dict = {k: v for k, v in chr_dict.items() if k in chr_list}

    return chr_dict


def load_clipped_read_positions_by_chr(sampleName, chrName, chr_dict, win_hlen, channel_dir):

    def get_filepath(vec_type):
        return os.path.join(channel_dir, sampleName, vec_type, vec_type + '.json.gz')

    logging.info('Loading SR positions for Chr%s' % chrName)

    with gzip.GzipFile(get_filepath('split_read_pos'), 'rb') as fin:
        positions, locations = json.loads(fin.read().decode('utf-8'))

    with gzip.GzipFile(get_filepath('clipped_read_pos'), 'rb') as fin:
        positions_cr = json.loads(fin.read().decode('utf-8'))

    locations = [(chr1, pos1, chr2, pos2)
                 for chr1, pos1, chr2, pos2 in locations
                 if chr1 in chr_dict.keys() and chr2 in chr_dict.keys()
                 and win_hlen <= pos1 <= (chr_dict[chr1] - win_hlen)
                 and win_hlen <= pos2 <= (chr_dict[chr2] - win_hlen)]
    positions_cr_l = {
        int(k) + 1 for k, v in positions_cr.items() if v >= min_CR_support}
    positions_cr_r = {
        int(k) - 1 for k, v in positions_cr.items() if v >= min_CR_support}
    positions_cr = positions_cr_l | positions_cr_r
    locations = [(chr1, pos1, chr2, pos2)
                 for chr1, pos1, chr2, pos2 in locations
                 if (chr1 == chrName and pos1 in positions_cr) or (
                     chr2 == chrName and pos2 in positions_cr)]
    logging.info("%d positions" % len(locations))
    return locations


def load_all_clipped_read_positions_by_chr(sampleName, win_hlen, chr_dict, output_dir):
    cr_pos_file = os.path.join(
        output_dir, sampleName, 'candidate_positions_' + sampleName + '.json.gz')
    if os.path.exists(cr_pos_file):
        logging.info('Loading existing candidate positions file...')
        with gzip.GzipFile(cr_pos_file, 'rb') as fin:
            cpos_list = json.loads(fin.read().decode('utf-8'))
        return cpos_list

    cpos_list = []
    chrlist = get_chr_list()
    chr_list = chrlist if sampleName != 'T1' else ['17']
    for chrName in chr_list:
        logging.info("Loading candidate positions for Chr%s" % str(chrName))
        cpos = load_clipped_read_positions(
            sampleName, chrName, chr_dict, win_hlen, output_dir)
        cpos_list.extend(cpos)
        logging.info("Candidate positions for Chr%s: %d" %
                     (str(chrName), len(cpos)))
    logging.info("Writing candidate positions file %s" % cr_pos_file)
    with gzip.GzipFile(cr_pos_file, 'wb') as f:
        f.write(json.dumps(cpos_list).encode('utf-8'))
    return cpos_list


def load_all_clipped_read_positions(win_hlen, svtype, chr_dict, output_dir, clipped_type="SR"):
    config = get_config_file()
    min_CR_support = config["DEFAULT"]["MIN_CR_SUPPORT"]

    def get_filepath(vec_type):
        return os.path.join(output_dir, vec_type, vec_type + '.json.gz')

    logging.info('Loading SR positions')

    total_reads_coord_min_support = []
    chr_list = get_chr_list()

    with gzip.GzipFile(get_filepath('split_reads'), 'rb') as fin:
        positions_with_min_support_ls, positions_with_min_support_rs, total_reads_coord_min_support_json, \
            split_reads, split_read_distance = json.loads(
                fin.read().decode('utf-8'))

    with gzip.GzipFile(get_filepath('clipped_read_pos'), 'rb') as fin:
        left_clipped_pos_cnt, right_clipped_pos_cnt = json.loads(
            fin.read().decode('utf-8'))

    if svtype in total_reads_coord_min_support_json:
        if svtype in ('DEL', 'INDEL_DEL'):
            total_reads_coord_min_support = total_reads_coord_min_support_json['DEL'] + \
                total_reads_coord_min_support_json['INDEL_DEL']
        elif svtype in ('INS', 'INDEL_INS'):
            total_reads_coord_min_support = total_reads_coord_min_support_json['INS'] + \
                total_reads_coord_min_support_json['INDEL_INS']
        else:
            total_reads_coord_min_support = total_reads_coord_min_support_json[svtype]

    locations_sr = dict()
    locations_cr_r = dict()
    locations_cr_l = dict()
    positions_cr = dict()

    for chrom in chr_list:
        if clipped_type == 'SR':
            locations_sr[chrom] = [
                (chr1, pos1, chr2, pos2, strand_info)
                for chr1, pos1, chr2, pos2, strand_info in total_reads_coord_min_support
                if chr1 in chr_dict.keys() and chr2 in chr_dict.keys() and chr1
                == chrom and win_hlen <= pos1 <= (chr_dict[chr1] - win_hlen)
                and win_hlen <= pos2 <= (chr_dict[chr2] - win_hlen)
            ]
            logging.info("Chr%s: %d positions" %
                         (str(chrom), len(locations_sr[chrom])))

        elif clipped_type == 'CR':
            if chrom in left_clipped_pos_cnt.keys():
                positions_cr_l = {
                    int(k) for k, v in left_clipped_pos_cnt[chrom].items() if v >= min_CR_support}
            else:
                positions_cr_l = set()

            if chrom in right_clipped_pos_cnt.keys():
                positions_cr_r = {
                    int(k) for k, v in right_clipped_pos_cnt[chrom].items() if v >= min_CR_support}
            else:
                positions_cr_r = set()

            if len(positions_cr_r) > 0:
                locations_cr_r[chrom] = [
                    (chrom, pos) for pos in sorted(list(positions_cr_r))
                ]
            if len(positions_cr_l) > 0:
                locations_cr_l[chrom] = [
                    (chrom, pos) for pos in sorted(list(positions_cr_l))
                ]

    if clipped_type == 'SR':
        cpos_list = []
        for chrom in chr_list:
            if chrom in locations_sr.keys():
                cpos_list.extend(locations_sr[chrom])
        logging.info("%d candidate positions" % len(cpos_list))
        return cpos_list

    if clipped_type == 'CR':
        cpos_list_right = []
        cpos_list_left = []
        for chrom in chr_list:
            if chrom in locations_cr_r.keys():
                cpos_list_right.extend(locations_cr_r[chrom])
        for chrom in chr_list:
            if chrom in locations_cr_l.keys():
                cpos_list_left.extend(locations_cr_l[chrom])
        logging.info("Right-clipped: %d candidate positions" %
                     len(cpos_list_right))
        logging.info("Left-clipped: %d candidate positions" %
                     len(cpos_list_left))
        logging.info("Writing candidate positions file %s" % cr_pos_file)
        return cpos_list_right, cpos_list_left


def load_windows(win_file):
    npzfile = np.load(win_file, allow_pickle=True, mmap_mode='r')
    X = npzfile['data']
    y = npzfile['labels']
    y = y.item()
    return X, y


def save_windows(X, y, win_file):
    np.savez(file=win_file, data=X, labels=y)


def get_chr_dict(fasta_file):
    d = dict()
    with pysam.FastaFile(filename=fasta_file, filepath_index=fasta_file + '.fai') as fa:
        for i, seqid in enumerate(fa.references):
            d[seqid] = fa.lengths[i] - 1
        return d


def estimate_insert_size(ibam, pysam_bam, min_mapq):
    base = os.path.basename(ibam)
    prefix = os.path.splitext(base)[0]
    isize_out = os.path.join(os.path.dirname(
        ibam), prefix + '.insert_size.csv')
    isize_distr = []
    i = 0

    for read in pysam_bam.fetch():
        if (not read.is_unmapped) and read.mapping_quality >= min_mapq \
                and read.is_reverse != read.mate_is_reverse \
                and read.reference_name == read.next_reference_name:
            dist = abs(read.reference_start - read.next_reference_start)
            if dist < 10 ** 3:
                isize_distr.append(dist)
                if i == 2 * 10 ** 6:
                    break
                i += 1

    df = pd.DataFrame({'mean': [mean(isize_distr)],
                       'sd': [stdev(isize_distr)]})
    df.to_csv(isize_out, index=False)
    return df


def get_insert_size(ibam, pysam_bam, min_mapq):
    base = os.path.basename(ibam)
    prefix = os.path.splitext(base)[0]
    isize_file = os.path.join(os.path.dirname(ibam), prefix+'.insert_size.csv')
    if os.path.exists(isize_file):
        df = pd.read_csv(isize_file)
    else:
        df = estimate_insert_size(ibam, pysam_bam, min_mapq)
    return df.at[0, 'mean'], df.at[0, 'sd']
