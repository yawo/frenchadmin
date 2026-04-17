#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# PREPARE FOLDERS
cd "${PROJECT_ROOT}/data"
mkdir -p selected/legi selected/jade selected/bofip
mkdir -p experimental/legi experimental/jade experimental/bofip

# Keep reruns deterministic by cleaning previous smart selection artifacts.
find selected/legi -mindepth 1 -delete
find selected/jade -mindepth 1 -delete
find selected/bofip -mindepth 1 -delete
find experimental/legi -mindepth 1 -delete
find experimental/jade -mindepth 1 -delete
find experimental/bofip -mindepth 1 -delete

# EXTRACT  AND SELECT

## unzip all unprocessed from legi
cd experimental/legi
#find ../unprocessed/legi -type f -name "*.tar.gz" -exec  tar -xzf {} ';'
find ../../unprocessed/legi -type f -name "*.tar.gz" -print0 | xargs -0 -r -I{} -P 10 tar -xzf {}

## unzip all unprocessed from jade
cd ../jade
find ../../unprocessed/jade -type f -name "*.tar.gz" -print0 | xargs -0 -r -I{} -P 10 tar -xzf {}

## unzip all unprocessed from bofip
cd ../bofip
find ../../unprocessed/bofip -type f -name "*.tgz" -print0 | xargs -0 -r -I{} -P 10 tar -xzf {}

## isolate CGI, LPF, AN_1, AN_2, AN_3, AN_4, CIBS from legi
cd ../legi
rg -l -0 -g 'LEGI*.xml' 'LEGITEXT000006069577|LEGITEXT000006069583|LEGITEXT000044594668|LEGITEXT000006069569|LEGITEXT000006069574|LEGITEXT000006069576|LEGITEXT000044595989' | xargs -0 -r -P 10 cp -t ../../selected/legi/



## isolate Fiscal ruling from jade
cd ../jade
rg -l -0 -g 'CETA*.xml' 'SCT [^>]*?>19-' | xargs -0 -r -P 10 cp -t ../../selected/jade/

## keep BOFiP document.xml + data.html pairs with original hierarchy
cd ../bofip
while IFS= read -r -d '' html_file; do
  doc_dir="$(dirname "$html_file")"
  xml_file="${doc_dir}/document.xml"
  rel_dir="${doc_dir#./}"
  dest_dir="../../selected/bofip/${rel_dir}"

  mkdir -p "$dest_dir"
  cp "$html_file" "${dest_dir}/data.html"

  if [[ -f "$xml_file" ]]; then
    cp "$xml_file" "${dest_dir}/document.xml"
  fi
done < <(find . -type f -name "data.html" -print0)


# SELECT ALSO REFERENCES 
## List legi references

## Find and copy references  

## List jade references

## Find and copy references  

## List bofip references

## Find and copy references  


