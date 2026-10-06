"""Verify the author's preuploaded native asset before publishing a release."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import tomllib
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'standalone/windows/speech/native-binaries.json').read_text('utf-8'))
asset=manifest['release_asset']
tag=asset['url'].split('/releases/download/',1)[1].split('/',1)[0]
name=asset['name']
repository='T8mars/Comfyui-Index-Translate-T8'
pages=json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',
                                         f'repos/{repository}/releases?per_page=100']))
# GitHub's by-tag endpoint omits drafts, even when the caller can list them.
matches=[release for page in pages for release in page if release['tag_name']==tag]
assert len(matches)==1, f'Expected one precreated release for {tag}; found {len(matches)}.'
release=matches[0]
remote=next(item for item in release['assets'] if item['name']==name)
assert remote['size']==manifest['archive_bytes']
assert remote['digest']=='sha256:'+manifest['archive_sha256']
target=ROOT/'.build/native-release-validation'
target.mkdir(parents=True,exist_ok=True)
subprocess.run(['gh','release','download',tag,'--repo',repository,'--pattern',name,'--dir',str(target),'--clobber'],check=True)
file=target/name
assert file.stat().st_size==manifest['archive_bytes']
with file.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
assert actual==manifest['archive_sha256']
records={item['path']:item for item in manifest['files']}
with zipfile.ZipFile(file) as archive:
    assert len(archive.namelist())==len(records) and set(archive.namelist())==set(records)
    for info in archive.infolist():
        assert info.file_size==records[info.filename]['bytes']
        with archive.open(info) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        assert digest==records[info.filename]['sha256']
current_tag='v'+tomllib.loads((ROOT/'pyproject.toml').read_text('utf-8'))['project']['version']
if tag==current_tag:
    with (ROOT/'dist/SHA256SUMS.txt').open('a',encoding='utf-8') as stream:
        stream.write(f'{actual}  {name}\n')
# An unchanged native component can be pinned to an earlier public Release.
# Its digest remains in the manifest; SHA256SUMS lists this Release's files.
print(f'Prebuilt native asset from {tag} and all 25 CPU/CUDA/CRT files verified.')
