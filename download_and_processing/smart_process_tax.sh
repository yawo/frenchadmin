#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_ROOT="${PROJECT_ROOT}/data"
UNPROCESSED_ROOT="${DATA_ROOT}/unprocessed"
SELECTED_ROOT="${DATA_ROOT}/selected"
EXPERIMENTAL_ROOT="${DATA_ROOT}/experimental"
#EXPERIMENTAL_ROOT="/media/hotep/Data/code/legalfrance/experimental"
# PREPARE FOLDERS
mkdir -p \
  "${SELECTED_ROOT}/legi" "${SELECTED_ROOT}/jade" "${SELECTED_ROOT}/bofip" \
  "${EXPERIMENTAL_ROOT}/legi" "${EXPERIMENTAL_ROOT}/jade" "${EXPERIMENTAL_ROOT}/bofip"

# Keep reruns deterministic by cleaning previous smart selection artifacts.
# find "${SELECTED_ROOT}/legi" -mindepth 1 -delete
# find "${SELECTED_ROOT}/jade" -mindepth 1 -delete
# find "${SELECTED_ROOT}/bofip" -mindepth 1 -delete
# find "${EXPERIMENTAL_ROOT}/legi" -mindepth 1 -delete
# find "${EXPERIMENTAL_ROOT}/jade" -mindepth 1 -delete
# find "${EXPERIMENTAL_ROOT}/bofip" -mindepth 1 -delete

copy_rg_matches() {
  local source_dir="$1"
  local filename_glob="$2"
  local search_pattern="$3"
  local destination_dir="$4"

  set +e
  rg -l -0 -g "${filename_glob}" "${search_pattern}" "${source_dir}" | xargs -0 -r cp --update=none  -t "${destination_dir}/"
#  rg -l -0 -g "${filename_glob}" "${search_pattern}" "${source_dir}" | xargs -0 -r rsync -a --ignore-existing -q -t "${destination_dir}/" 
  
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e

  if [[ "${pipeline_status[0]}" -gt 1 || "${pipeline_status[1]}" -ne 0 ]]; then
    echo "Failed to copy filtered files from ${source_dir}" >&2
    exit 1
  fi
}

process_legi_archive() {
  local archive_path="$1"
  local work_dir

  work_dir="$(mktemp -d "${EXPERIMENTAL_ROOT}/legi/archive.XXXXXX")"
  tar -xzf "${archive_path}" -C "${work_dir}"
  rm -f "${archive_path}"

  copy_rg_matches \
    "${work_dir}" \
    "LEGI*.xml" \
    "LEGITEXT000006069577|LEGITEXT000006069583|LEGITEXT000044594668|LEGITEXT000006069569|LEGITEXT000006069574|LEGITEXT000006069576|LEGITEXT000044595989" \
    "${SELECTED_ROOT}/legi"

  rm -rf "${work_dir}"
}

process_jade_archive() {
  local archive_path="$1"
  local work_dir

  work_dir="$(mktemp -d "${EXPERIMENTAL_ROOT}/jade/archive.XXXXXX")"
  tar -xzf "${archive_path}" -C "${work_dir}"
  rm -f "${archive_path}"

  copy_rg_matches \
    "${work_dir}" \
    "CETA*.xml" \
    "SCT [^>]*?>19-" \
    "${SELECTED_ROOT}/jade"

  rm -rf "${work_dir}"
}

process_bofip_archive() {
  local archive_path="$1"
  local work_dir

  work_dir="$(mktemp -d "${EXPERIMENTAL_ROOT}/bofip/archive.XXXXXX")"
  tar -xzf "${archive_path}" -C "${work_dir}"
  rm -f "${archive_path}"

  #while IFS= read -r -d '' html_file; do
     #local doc_dir xml_file rel_dir dest_dir
     #doc_dir="$(dirname "${html_file}")"
     #xml_file="${doc_dir}/document.xml"
     #if [[ "${doc_dir}" == "${work_dir}" ]]; then
      # rel_dir="."
     #else
      # rel_dir="${doc_dir#${work_dir}/}"
     #fi

     #if [[ "${rel_dir}" == "." ]]; then
      # dest_dir="${SELECTED_ROOT}/bofip"
     #else
       #dest_dir="${SELECTED_ROOT}/bofip/${rel_dir}"
     #fi

    # mkdir -p "${dest_dir}"
    # #rsync -a --ignore-existing -q  "${html_file}" "${dest_dir}/data.html"
     #cp --update=none  "${html_file}" "${dest_dir}/data.html"

    # if [[ -f "${xml_file}" ]]; then
      #rsync -a --ignore-existing "${xml_file}" "${dest_dir}/document.xml"
       #cp --update=none  "${xml_file}" "${dest_dir}/document.xml"
     #fi
   #done < <(find "${work_dir}" -type f -name "data.html" -print0)

  mv "${work_dir}" "${SELECTED_ROOT}/bofip/"
}

# EXTRACT, SELECT, DELETE (one archive at a time)
while IFS= read -r -d '' archive; do
  process_legi_archive "${archive}"
done < <(find "${UNPROCESSED_ROOT}/legi" -type f -name "*.tar.gz" -print0 | sort -z)

while IFS= read -r -d '' archive; do
  process_jade_archive "${archive}"
done < <(find "${UNPROCESSED_ROOT}/jade" -type f -name "*.tar.gz" -print0 | sort -z)

while IFS= read -r -d '' archive; do
  process_bofip_archive "${archive}"
done < <(find "${UNPROCESSED_ROOT}/bofip" -type f -name "*.tgz" -print0 | sort -z)


# SELECT ALSO REFERENCES 
## List legi references

## Find and copy references  

## List jade references

## Find and copy references  

## List bofip references

## Find and copy references  
