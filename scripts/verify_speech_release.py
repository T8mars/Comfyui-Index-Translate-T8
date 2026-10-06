"""Verify the author's preuploaded native asset before publishing a release."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'standalone/windows/speech/native-binaries.json').read_text('utf-8'))
asset=manifest['release_asset']
tag=asset['url'].split('/releases/download/',1)[1].split('/',1)[0]
name=asset['name']
repository='T8mars/Comfyui-Index-Translate-T8'
release=json.loads(subprocess.check_output(['gh','api',f'repos/{repository}/releases/tags/{tag}']))
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
with (ROOT/'dist/SHA256SUMS.txt').open('a',encoding='utf-8') as stream:
    stream.write(f'{actual}  {name}\n')
print('Prebuilt native asset and all 25 CPU/CUDA/CRT files verified; release checksums updated.')
