import argparse
import gzip
import json
import logging
import os
import statistics
from collections import defaultdict
from time import time

import bcolz as bz
import numpy as np
import pyBigWig
import pysam

from functions import *


def get_chr_len(bam_file, chrom):
    with pysam.AlignmentFile(bam_file, 'rb') as bam:
        return [i['LN'] for i in bam.header['SQ'] if i['SN'] == chrom][0]


def count_clipped_read_positions(cpos_cnt):
    '''
    :param cpos_cnt: dictionary of clipped read positions (keys) and counts of clipped reads per position (values) as
    returned by the clipped_read_pos.py script
    :return: None. Prints the number of clipped read positions with clipped read support greater than the integers
    specified in the range
    '''
    for i in range(0, 5):
        logging.info('Number of positions with at least %d clipped reads: %d' %
                     (i + 1, len([k for k, v in cpos_cnt.items() if v > i])))


def load_channel(chr_list, outDir, ch):
    channel_names_wg = ['split_reads', 'clipped_reads']
    channel_names = ['coverage', 'clipped_read_distance', 'snv']
    channel_data = defaultdict(dict)

    if ch in channel_names_wg:
        suffix = '.json.gz'
        filename = os.path.join(outDir, ch, ch + suffix)
        with gzip.GzipFile(filename, 'r') as fin:
            logging.info('Reading %s...' % ch)
            if ch == 'split_reads':
                positions_with_min_support_ls, positions_with_min_support_rs, total_reads_coord_min_support, split_reads, split_read_distance = json.loads(
                    fin.read().decode('utf-8'))
            elif ch == 'clipped_reads':
                clipped_reads, clipped_reads_inversion, clipped_reads_duplication, clipped_reads_translocation = json.loads(
                    fin.read().decode('utf-8'))

        for chrom in chr_list:
            if ch == 'split_reads':
                channel_data[chrom]['split_reads'] = split_reads[chrom]
                channel_data[chrom][
                    'split_read_distance'] = split_read_distance[chrom]
                del split_reads, split_read_distance
            elif ch == 'clipped_reads':
                channel_data[chrom]['clipped_reads'] = clipped_reads[chrom]
                channel_data[chrom][
                    'clipped_reads_inversion'] = clipped_reads_inversion[chrom]
                channel_data[chrom][
                    'clipped_reads_duplication'] = clipped_reads_duplication[
                    chrom]
                channel_data[chrom][
                    'clipped_reads_translocation'] = clipped_reads_translocation[
                    chrom]
                del clipped_reads, clipped_reads_inversion, \
                    clipped_reads_duplication, clipped_reads_translocation

    elif ch in channel_names:
        logging.info('Loading data for channel %s' % ch)
        for chrom in chr_list:
            logging.info('Loading data for Chr%s' % chrom)
            suffix = '.npy.gz' if ch in ['snv', 'coverage'] else '.json.gz'
            filename = os.path.join(outDir, ch, '_'.join([chrom, ch + suffix]))
            assert os.path.isfile(filename), filename + " does not exists!"

            logging.info('Reading %s for Chr%s' % (ch, chrom))

            if suffix == '.npy.gz':
                with gzip.GzipFile(filename, 'r') as fin:
                    channel_data[chrom][ch] = np.load(fin)
            else:
                with gzip.GzipFile(filename, 'r') as fin:
                    channel_data[chrom][ch] = json.loads(
                        fin.read().decode('utf-8'))
            logging.info('End of reading')
    return channel_data


def create_carray(ibam, chrom, twobit, outDir, cmd_name):

    chrlen = get_chr_len(ibam, chrom)
    channel_index = 0
    n_channels = 53

    chr_array = np.zeros(shape=(chrlen, n_channels), dtype=np.float64)

    # dictionary of key choices
    direction_list = {
        'clipped_reads': [
            'left_F', 'left_R', 'right_F', 'right_R', 'disc_left_F',
            'disc_left_R', 'disc_right_F', 'disc_right_R', 'D_left_F',
            'D_left_R', 'D_right_F', 'D_right_R', 'I_F', 'I_R'
        ],
        'split_reads': ['left_F', 'left_R', 'right_F', 'right_R'],
        'split_read_distance': ['left_F', 'left_R', 'right_F', 'right_R'],
        'clipped_reads_inversion': ['before', 'after', 'before_split', 'after_split'],
        'clipped_reads_duplication': ['before', 'after', 'before_split', 'after_split'],
        'clipped_reads_translocation': ['opposite', 'same', 'opposite_split', 'same_split'],
        'clipped_read_distance': ['forward', 'reverse']
    }

    channels = ['coverage', 'snv', 'clipped_reads', 'split_reads', 'clipped_read_distance',
                'clipped_reads_inversion', 'clipped_reads_duplication', 'clipped_reads_translocation', 'split_read_distance']

    for current_channel in channels:
        current_channel_dataset = current_channel
        if current_channel in ['split_reads', 'split_read_distance']:
            current_channel_dataset = 'split_reads'
        elif current_channel in ['clipped_reads', 'clipped_reads_inversion', 'clipped_reads_duplication', 'clipped_reads_translocation']:
            current_channel_dataset = 'clipped_reads'

        channel_data = load_channel([chrom], outDir, current_channel_dataset)
        logging.info("Adding channel %s at index %d" %
                     (current_channel, channel_index))

        if current_channel in ('coverage', 'snv'):

            # logging.info("snv array shape %d" % channel_data[chrom][current_channel].shape[1])
            # if current_channel == 'snv' and channel_data[chrom][current_channel].shape[1] == 2:
            #     channel_data[chrom][current_channel] = np.delete(channel_data[chrom][current_channel], 2, 0)

            ch_num = channel_data[chrom][current_channel].shape[1]

            chr_array[:, channel_index:channel_index +
                      ch_num] = channel_data[chrom][current_channel][:
                                                                     chrlen, :]
            channel_index += ch_num
            del channel_data[chrom][current_channel]

        elif current_channel in ('clipped_reads', 'split_reads', 'clipped_reads_inversion', 'clipped_reads_duplication', 'clipped_reads_translocation'):
            for split_direction in direction_list[current_channel]:
                if len(channel_data[chrom][current_channel]
                       [split_direction]) > 0:
                    idx = np.fromiter(channel_data[chrom][current_channel]
                                      [split_direction].keys(),
                                      dtype=int)
                    vals = np.fromiter(channel_data[chrom][current_channel]
                                       [split_direction].values(),
                                       dtype=np.float32)
                    if len(idx) > 0:
                        chr_array[idx, channel_index] = vals
                        assert chr_array[idx, channel_index].any(), \
                            print('{}:{} is all zeros!'.format(
                                current_channel, split_direction))

                channel_index += 1
                del channel_data[chrom][current_channel][split_direction]

        elif current_channel == 'clipped_read_distance':
            for split_direction in direction_list[current_channel]:
                for clipped_arrangement in ['left', 'right', 'all']:
                    idx = np.array(
                        list(
                            map(
                                int, channel_data[chrom][current_channel]
                                [split_direction]
                                [clipped_arrangement].keys())))

                    vals = np.array(
                        list(
                            map(
                                statistics.median, channel_data[chrom]
                                [current_channel][split_direction]
                                [clipped_arrangement].values())))

                    if len(idx) > 0:
                        chr_array[idx, channel_index] = vals
                    channel_index += 1

                    del channel_data[chrom][current_channel][split_direction][
                        clipped_arrangement]

        elif current_channel == 'split_read_distance':
            for split_direction in direction_list[current_channel]:
                idx = np.array(
                    list(
                        map(
                            int, channel_data[chrom][current_channel]
                            [split_direction].keys())))

                vals = np.array(
                    list(
                        map(
                            statistics.median, channel_data[chrom]
                            [current_channel][split_direction].values())))
                if len(idx) > 0:
                    chr_array[idx, channel_index] = vals
                channel_index += 1

                del channel_data[chrom][current_channel][split_direction]

    current_channel = 'one_hot_encoding'
    logging.info("Adding channel %s at index %d" %
                 (current_channel, channel_index))
    nuc_list = ['A', 'T', 'C', 'G', 'N']
    chr_array[:, channel_index:channel_index +
              len(nuc_list)] = get_one_hot_sequence_by_list(twobit, chrom, list(np.arange(chrlen)))
    channel_index += len(nuc_list)
    logging.info("chr_array shape: %s" % str(chr_array.shape))
    outfile = os.path.join(outDir, cmd_name, chrom + '_carray')
    logging.info("Writing carray...")
    a = bz.carray(chr_array, rootdir=outfile, mode='w')
    a.flush()


def main():
    default_chr = '22'
    parser = argparse.ArgumentParser(
        description='Create channels from saved data')
    parser.add_argument('-b',
                        '--bam',
                        type=str,
                        default='../../data/test.bam',
                        help="Specify input file (BAM)")
    parser.add_argument('-c',
                        '--chr',
                        type=str,
                        default=default_chr,
                        help="Specify chromosome")
    parser.add_argument('-t',
                        '--twobit',
                        type=str,
                        default='../../data/test.2bit',
                        help="Specify input file (2bit)")
    parser.add_argument('-o',
                        '--out',
                        type=str,
                        default='chr_array/'+default_chr+'_chr_array',
                        help="Specify output")
    parser.add_argument(
        '-p',
        '--outputpath',
        type=str,
        default='.',
        help="Specify output path")
    parser.add_argument('-l',
                        '--logfile',
                        default='chr_array.log',
                        help='File in which to write logs.')
    parser.add_argument('-w',
                        '--window',
                        type=str,
                        default=200,
                        help="Specify window size")

    args = parser.parse_args()
    cmd_name = 'chr_array'
    output_dir = os.path.join(args.outputpath, cmd_name)
    os.makedirs(output_dir, exist_ok=True)
    logfilename = os.path.join(output_dir, args.chr+'_'+args.logfile)
    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(format=FORMAT,
                        filename=logfilename,
                        filemode='w',
                        level=logging.INFO)

    t0 = time()
    create_carray(ibam=args.bam,
                  chrom=args.chr,
                  twobit=args.twobit,
                  outDir=args.outputpath,
                  cmd_name=cmd_name)
    logging.info('Elapsed time channel_maker_real = %f mins' % (time() - t0))


if __name__ == '__main__':
    main()
