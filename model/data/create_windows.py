import argparse
import errno
import gzip
import itertools
import json
import logging
import os
from collections import Counter
from time import time

# import sparse
import bcolz
import dask.array as da
import h5py
import numpy as np


def get_range(dictionary, begin, end):
    return dict(itertools.islice(dictionary.items(), begin, end))


def create_dir(directory):
    '''
    Create a directory if it does not exist. Raises an exception if the directory exists.
    :param directory: directory to create
    :return: None
    '''
    try:
        os.makedirs(directory)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def get_chr_list(sampleName):

    if sampleName == 'T1':
        chrlist = ['17']
    else:
        chrlist = list(map(str, range(1, 23)))
        chrlist.extend(['X'])

    return chrlist


def load_chr_array(channel_data_dir, sampleName):
    chrlist = get_chr_list(sampleName)
    chr_array = dict()

    for c in chrlist:
        chrname = '/chr' + c
        hdf5_file = os.path.join(channel_data_dir, sampleName, 'chr_array',
                                 sampleName + '_' + c + '.hdf5')
        logging.info('Loading file: {}'.format(hdf5_file))
        assert os.path.exists(hdf5_file), hdf5_file + ' not found'
        f = h5py.File(hdf5_file)
        d = f[chrname]
        logging.info('Chunks: {}'.format(d.chunks))

        if d.chunks is None:
            chr_array[c] = da.from_array(d, chunks=(1000000, None))
        else:
            chr_array[c] = da.from_array(d, chunks=d.chunks)
        # chr_array[c] = chr_array[c].map_blocks(sparse.COO)

    return chr_array


def get_labels(channel_data_dir, sampleName, win):
    label_file = os.path.join(channel_data_dir, sampleName,
                              'labels_win' + str(win), 'labels.json.gz')

    with gzip.GzipFile(label_file, 'r') as fin:
        labels = json.loads(fin.read().decode('utf-8'))

    return labels


def split_labels(labels):
    p = {}
    n = {}
    for k, v in labels.items():

        if v == 'DEL':
            p[k] = v
        elif v == 'noDEL':
            n[k] = v
    return p, n


def unfold_win_id(win_id):
    chr1, pos1, chr2, pos2 = win_id.split('_')
    pos1 = int(pos1)
    pos2 = int(pos2)
    return chr1, pos1, chr2, pos2


def get_window_by_id(win_id, chr_array, padding, win_hlen):
    chr1, pos1, chr2, pos2 = win_id.split('_')
    pos1 = int(pos1)
    pos2 = int(pos2)

    dask_arrays = list()
    dask_arrays.append(chr_array[chr1][pos1 - win_hlen:pos1 + win_hlen, :])
    dask_arrays.append(padding)
    dask_arrays.append(chr_array[chr2][pos2 - win_hlen:pos2 + win_hlen, :])
    return da.concatenate(dask_arrays, axis=0)


def get_windows(sampleName, outDir, win, cmd_name, mode):
    def same_chr_in_winid(win_id):
        chr1, pos1, chr2, pos2 = win_id.split('_')
        return chr1 == chr2

    outfile_dir = os.path.join(outDir, sampleName, cmd_name)

    chr_array = load_chr_array(outDir, sampleName)
    n_channels = chr_array['17'].shape[1]
    labels = get_labels(outDir, sampleName, win)
    # labels = get_range(labels, 0, 10000)

    if sampleName == 'T1':
        labels = {k: v for k, v in labels.items() if same_chr_in_winid(k)}

    logging.info('{} labels found: {}'.format(len(labels),
                                              Counter(labels.values())))

    if mode == 'training':

        labels_positive = {k: v for k, v in labels.items() if v == 'DEL'}
        labels_negative = {k: v for k, v in labels.items() if v == 'noDEL'}
        labels_negative = get_range(labels_negative, 0,
                                    len(labels_positive.keys()))
        labels_set = {'positive': labels_positive, 'negative': labels_negative}
        # labels_set = {'negative': labels_negative}

    elif mode == 'test':

        labels_set = {'test': labels}

    padding_len = 10
    win_hlen = int(int(win) / 2)

    for labs_name, labs in labels_set.items():

        logging.info('Creating {}...'.format(labs_name))

        dask_arrays_win1 = list()
        dask_arrays_win2 = list()

        n_r = 10**5
        # print(n_r)
        last_t = time()
        i = 1

        logging.info('Creating dask_arrays_win1 and dask_arrays_win2...')
        for chr1, pos1, chr2, pos2 in map(unfold_win_id, labs.keys()):

            if not i % n_r:
                # Record the current time
                now_t = time()
                # print(type(now_t))
                logging.info(
                    "%d window pairs processed (%f window pairs / s)" %
                    (i, n_r / (now_t - last_t)))
                last_t = time()

            d = chr_array[chr1][pos1 - win_hlen:pos1 + win_hlen, :]
            dask_arrays_win1.append(d)
            d = chr_array[chr2][pos2 - win_hlen:pos2 + win_hlen, :]
            dask_arrays_win2.append(d)
            i += 1

        padding = da.zeros(shape=(len(labs.keys()), padding_len, n_channels),
                           dtype=np.float32)
        dask_array = list()
        logging.info('Stacking dask_arrays_win1...')
        dask_array.append(da.stack(dask_arrays_win1, axis=0))
        dask_array.append(padding)
        logging.info('Stacking dask_arrays_win2...')
        dask_array.append(da.stack(dask_arrays_win2, axis=0))
        logging.info('Concatenating...')
        dask_array = da.concatenate(dask_array, axis=1)
        # logging.info('Rechunking...')
        # dask_array = dask_array.rechunk({0: 'auto', 1: None, 2: None})

        outfile = os.path.join(outfile_dir, labs_name)

        logging.info('Writing...')
        # np.savez_compressed(file=outfile,
        #                     data=dask_array.compute())
        # da.to_hdf5(outfile + '.hdf5',
        #            '/data', dask_array)
        # compression='lzf')
        # dask_array.to_hdf5(outfile + '.hdf5', '/data')

        # f = h5py.File(outfile+'.hdf5')
        # d = f.require_dataset('/data', shape=dask_array.shape, dtype=dask_array.dtype)
        # da.store(dask_array, d)

        a = bcolz.carray(dask_array, rootdir=outfile + '_carray', mode='w')
        a.attrs['labels'] = labs
        a.flush()

        # logging.info('Writing labels to JSON...')
        # with gzip.GzipFile(outfile + '_labels.json.gz', 'wb') as fout:
        #     fout.write(json.dumps(labs).encode('utf-8'))


def main():
    '''
    Main function for parsing the input arguments and calling the function to create windows
    :return: None
    '''

    # Default BAM file for testing
    # On the HPC
    # wd = '/hpc/cog_bioinf/ridder/users/lsantuari/Datasets/DeepSV/'+
    #   'artificial_data/run_test_INDEL/samples/T0/BAM/T0/mapping'
    # inputBAM = wd + "T0_dedup.bam"
    # Locally
    wd = '/Users/lsantuari/Documents/Data/HPC/DeepSV/Artificial_data/run_test_INDEL/BAM/'
    inputBAM = wd + "T1_dedup.bam"

    parser = argparse.ArgumentParser(
        description='Create windows from chromosome arrays')
    parser.add_argument('-b',
                        '--bam',
                        type=str,
                        default=inputBAM,
                        help="Specify input file (BAM)")
    parser.add_argument(
        '-p',
        '--outputpath',
        type=str,
        default='/Users/lsantuari/Documents/Processed/channel_maker_output',
        help="Specify output path")
    parser.add_argument('-s',
                        '--sample',
                        type=str,
                        default='T1',
                        help="Specify sample")
    parser.add_argument('-l',
                        '--logfile',
                        default='windows.log',
                        help='File in which to write logs.')
    parser.add_argument('-w',
                        '--window',
                        type=str,
                        default=200,
                        help="Specify window size")
    parser.add_argument('-m',
                        '--mode',
                        type=str,
                        default='training',
                        help="training/test mode")

    args = parser.parse_args()

    cmd_name = 'windows'
    output_dir = os.path.join(args.outputpath, args.sample, cmd_name)
    create_dir(output_dir)
    logfilename = os.path.join(output_dir, args.logfile)
    # output_file = os.path.join(output_dir, args.out)

    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(format=FORMAT,
                        filename=logfilename,
                        filemode='w',
                        level=logging.INFO)

    t0 = time()

    get_windows(sampleName=args.sample,
                outDir=args.outputpath,
                win=args.window,
                cmd_name=cmd_name,
                mode=args.mode)

    # print('Elapsed time channel_maker_real on BAM %s and Chr %s = %f' % (args.bam, args.chr, time() - t0))
    logging.info('Elapsed time create_windows = %f seconds' % (time() - t0))


if __name__ == '__main__':
    main()