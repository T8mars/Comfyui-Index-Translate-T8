"""Author-side packaging of already compiled native speech libraries."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import shutil
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / 'standalone/windows'
parser = argparse.ArgumentParser()
parser.add_argument('--cuda-build', type=Path, required=True)
parser.add_argument('--cpu-build', type=Path, required=True)
parser.add_argument('--crt-dir', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(PRODUCT))
from speech.r2t2_core.native_bundle import MEMBERS, MAX_PAYLOAD

# Needed before the worker imports its audio/ONNX extensions, and before
# the main service imports Torch. Old updaters accept small files in app/*.
crt_target = PRODUCT / 'app/dlls'
crt_target.mkdir(parents=True, exist_ok=True)
for name in sorted(item.removeprefix('crt/') for item in MEMBERS if item.startswith('crt/')):
    shutil.copyfile(args.crt_dir / name, crt_target / name)

records = []
payload = io.BytesIO()
with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for name in sorted(MEMBERS):
        if name.startswith('crt/'):
            source = args.crt_dir / Path(name).name
        elif name.startswith('cpu/'):
            source = args.cpu_build / name.removeprefix('cpu/')
        else:
            source = args.cuda_build / name
        data = source.read_bytes()
        assert data.startswith(b'MZ'), source
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        records.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
content = payload.getvalue()
assert len(content) <= MAX_PAYLOAD, f'Native payload exceeds legacy updater limit: {len(content)}'
target = PRODUCT / 'speech'
parts = []
for index, offset in enumerate(range(0, len(content), 8 * 1024 * 1024)):
    data = content[offset:offset + 8 * 1024 * 1024]
    name = f'native-binaries.{index:03d}.bin'
    (target / name).write_bytes(data)
    parts.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
expected = {item['path'] for item in parts}
for old in target.glob('native-binaries.*.bin'):
    if old.name not in expected:
        old.unlink()
archive_sha = hashlib.sha256(content).hexdigest()
version=(PRODUCT/'VERSION').read_text('utf-8').strip()
asset_name=f'IndexTranslate-Speech-native-v{version}.zip'
asset_directory=ROOT/'.build/native-release-assets'
asset_directory.mkdir(parents=True,exist_ok=True)
(asset_directory/asset_name).write_bytes(content)
manifest = {'schema': 1, 'build_id': 'llama-ad6c66839af3-multi-cu128-cpu-' + archive_sha[:16],
            'llama_cpp_commit': 'ad6c66839af3c5646fba8c6c2e2087a1e4e38948',
            'python_abi': 'cp312-win_amd64', 'cuda_toolkit': '12.8.61', 'cuda_driver_min': 12080,
            'cuda_architectures': ['50-virtual', '61-virtual', '70-virtual', '75-virtual',
                                   '80-virtual', '86-real', '89-real', '90-virtual', '120a-real'],
            'cpu_baseline': 'x64 SSE4.2 (GGML_NATIVE=OFF, AVX/AVX2/BMI2=OFF)',
            'cpu_backend_cuda_dependency': False, 'vc_runtime_version': '14.51.36247.0',
            'vc_runtime_source': 'https://aka.ms/vc14/vc_redist.x64.exe',
            'archive_sha256': archive_sha, 'archive_bytes': len(content),
            'release_asset': {'name': asset_name,
                'url': f'https://github.com/T8mars/Comfyui-Index-Translate-T8/releases/download/v{version}/{asset_name}'},
            'parts': parts, 'files': records}
(target / 'native-binaries.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps({'build_id': manifest['build_id'], 'files': len(records),
                  'expanded_bytes': sum(item['bytes'] for item in records),
                  'archive_bytes': len(content), 'parts': len(parts)}, indent=2))
