#!/usr/bin/env bash

set -xe

# check input arg(s)
if [ $# != 2 ]; then
  echo "Usage: $0 [SCHEDULER {gridengine,slurm}] [BAM file]"
  exit 1
fi

# set [env] variables
SCH=$1
BAM=$2
SAMPLE=$(basename $BAM .bam)
PRGS=(clipped_read_pos clipped_reads split_reads)
JOBS=()
WORK_DIR=$HOME/scripts/genome_wide

source ~/.profile
cd $WORK_DIR
printenv
xenon --version

# write channels into *.json.gz files
for p in ${PRGS[@]}; do
  CMD="python $p.py --bam $BAM --out $p.json.gz --outputpath ."
  JOB_ID=$(xenon -v scheduler $SCH --location local:// submit \
    --name $SAMPLE_$p --cores-per-task 1 --inherit-env --max-run-time 1 \
    --working-directory . --stderr stderr.log --stdout stdout.log "$CMD")
  JOBS+=($JOB_ID)
done

# fetch job accounting info
sleep 10
for j in ${JOBS[@]}; do
   xenon -v scheduler $SCH --location local:// list --identifier $j
done

# write stdout/stderr logs into terminal
echo -e "\nLog files:"
for f in *.log; do
  echo "### $f ###"
  cat $f
done

# list channel outfiles (*.json.gz)
echo -e "\nOutput files:"
#ls
find -type f -name *.json.gz | grep "." || exit 1
