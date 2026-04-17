#!/bin/bash
# PREPARE FOLDERS
cd data
mkdir selected
mkdir selected/legi
mkdir selected/jade
mkdir selected/bofip
mkdir experimental
mkdir experimental/legi
mkdir experimental/jade
mkdir experimental/bofip

# EXTRACT  AND SELECT

## unzip all unprocessed from legi
cd experimental/legi
#find ../unprocessed/legi -type f -name "*.tar.gz" -exec  tar -xzf {} ';'
find ../../unprocessed/legi -type f -name "*.tar.gz" -print0 |  xargs -0 -I{} -P 10 tar -xzf {} 

## unzip all unprocessed from jade
cd ../jade
find ../../unprocessed/jade -type f -name "*.tar.gz" -print0 |  xargs -0 -I{} -P 10 tar -xzf {} 

## unzip all unprocessed from bofip
cd ../bofip
find ../../unprocessed/bofip -type f -name "*.tgz" -print0 |  xargs -0 -I{} -P 10 tar -xzf {} 

## isolate CGI, LPF, AN_1, AN_2, AN_3, AN_4, CIBS from legi
cd ../legi
rg -l -0 -g '*.xml' 'LEGITEXT000006069577|LEGITEXT000006069583|LEGITEXT000044594668|LEGITEXT000006069569|LEGITEXT000006069574|LEGITEXT000006069576|LEGITEXT000044595989' | xargs -0 -P 10 cp -t ../../selected/legi/



## isolate Fiscal ruling from jade
cd ../jade
rg -l -0 -g '*.xml' 'SCT [^>]*?>19-' | xargs -0 -P 10 cp -t ../../selected/jade/

## taking all bofip is ok (.*)
cd ../bofip
rg -l -0 -g '*.html' '.*' | xargs -0 -I{} -P 10 sh -c '
  f="$1"
  new=$(echo "$f" | sed "s|[/ ;]|_|g")
  cp "$f" "../../selected/bofip/$new"
' _ {}


# SELECT ALSO REFERENCES 
## List legi references

## Find and copy references  

## List jade references

## Find and copy references  

## List bofip references

## Find and copy references  


