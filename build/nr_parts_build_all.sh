#!/bin/bash
# Build the nr parts p1..p6 from the nr_acc_scan output, two streams in parallel (different target disks).
# Stream A on / : p1, p2, p3, p5.  Stream B: p4 on /mnt/archive, p6 on /mnt/usbparts.
cd ~/python
SCAN=/mnt/archive/nr_split
DB=$HOME/build/diamond-master-61bbbe491bed/build/diamond
LOG=/mnt/archive/gh_tmp/nr_parts_build
mkdir -p $LOG
b() {  # part dir groups
  PART_DIR=$2 DIAMOND_BIN=$DB TMPDIR=/mnt/archive/gh_tmp nice -n 5 python3 nr_part_build.py \
    --part $1 --scan $SCAN --groups $3 > $LOG/$1.log 2>&1
  echo "$(date '+%F %T') $1 exit $?" >> $LOG/status.txt
}
(
  b p1_mammalia /home/gh/diamond/parts mammalia
  b p2_eukaryota_other /home/gh/diamond/parts metazoa,fungi,plants,eukaryota
  b p3_viruses_archaea_other /home/gh/diamond/parts viruses,archaea,none,other
  b p5_bacillati /home/gh/diamond/parts actinomycetota,bacillota,chloroflexota,cyanobacteriota,mycoplasmatota
) &
(
  b p4_pseudomonadota /mnt/archive/parts gammaproteobacteria,alphaproteobacteria,betaproteobacteria,pseudomonadota
  b p6_bacteria_other /mnt/usbparts bacteria,bacteroidota,planctomycetota,campylobacterota,verrucomicrobiota,spirochaetota
) &
wait
echo "$(date '+%F %T') all done" >> $LOG/status.txt
