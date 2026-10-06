"""Install the prebuilt native payload and choose a safe speech backend."""
from __future__ import annotations

import ctypes
import hashlib
import http.client
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.runtime/build-native-cu128'
MAX_PAYLOAD = 192 * 1024 * 1024
MAX_EXPANDED = 768 * 1024 * 1024
GPU_MEMBERS = frozenset({
    'bin/Release/ggml.dll', 'bin/Release/ggml-base.dll',
    'bin/Release/ggml-cpu.dll', 'bin/Release/ggml-cuda.dll',
    'bin/Release/llama.dll', 'bin/Release/mtmd.dll',
    'python/Release/qwen3asr_native.cp312-win_amd64.pyd',
})
MEMBERS = GPU_MEMBERS | frozenset('cpu/' + name for name in GPU_MEMBERS
                                if name != 'bin/Release/ggml-cuda.dll')
MEMBERS |= frozenset('crt/' + name for name in (
    'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vccorlib140.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll',
    'vcomp140.dll', 'vcamp140.dll'))


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _installed(build: Path, manifest: dict) -> bool:
    return all((build / entry['path']).resolve().is_relative_to(build.resolve()) and
               (build / entry['path']).is_file() and
               (build / entry['path']).stat().st_size == entry['bytes'] and
               _hash(build / entry['path']) == entry['sha256']
               for entry in manifest['files'])


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub assets use signed HTTPS CDN redirects; never follow other hosts."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != 'https' or target.netloc not in
                {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}):
            raise RuntimeError('语音原生库下载重定向不受信任，请重新下载最新更新包')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _release_url(manifest: dict) -> str:
    asset = manifest.get('release_asset')
    if not isinstance(asset, dict) or not isinstance(asset.get('name'), str) or not isinstance(asset.get('url'), str):
        raise RuntimeError('语音原生库分片缺失，且没有有效的预编译下载地址；请重新下载完整整合包')
    match = re.fullmatch(r'IndexTranslate-Speech-native-(v[0-9]+\.[0-9]+\.[0-9]+)\.zip', asset['name'])
    if match is None:
        raise RuntimeError('语音原生库下载文件名无效，请重新下载最新更新包')
    expected = ('https://github.com/T8mars/Comfyui-Index-Translate-T8/releases/download/'
                + match.group(1) + '/' + asset['name'])
    if asset['url'] != expected:
        raise RuntimeError('语音原生库下载地址不受信任，请重新下载最新更新包')
    return expected


def _download_archive(manifest: dict, destination: Path, expected_bytes: int) -> None:
    url = _release_url(manifest)
    # Keep the user's normal proxy configuration local to this opener.
    opener = urllib.request.build_opener(_ReleaseRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'T8-IndexTranslate-native/0.1.21',
                                                   'Accept': 'application/octet-stream'})
    try:
        response = opener.open(request, timeout=30)
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        # Redirect errors may contain a signed URL or proxy credentials.
        raise RuntimeError('语音原生库下载连接失败，请检查网络或代理后重试；旧库未被替换') from None
    digest, received = hashlib.sha256(), 0
    with response, destination.open('wb') as target:
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length) != expected_bytes):
            raise RuntimeError('语音原生库下载大小与清单不符，请重试或下载完整整合包')
        while True:
            try:
                block = response.read(1024 * 1024)
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                raise RuntimeError('语音原生库下载中断，请检查网络或代理后重试；旧库未被替换') from None
            if not block:
                break
            received += len(block)
            if received > expected_bytes or received > MAX_PAYLOAD:
                raise RuntimeError('语音原生库下载超出清单大小限制，已取消下载')
            digest.update(block)
            target.write(block)
    if received != expected_bytes:
        raise RuntimeError('语音原生库下载不完整，请检查网络后重试；旧库未被替换')
    if digest.hexdigest() != manifest['archive_sha256']:
        raise RuntimeError('语音原生库下载校验失败，请重试或下载完整整合包；旧库未被替换')


def ensure_native_bundle(build: Path = BUILD, root: Path = ROOT) -> dict:
    """Use verified offline parts or a pinned prebuilt asset; never compile."""
    root, build = Path(root).resolve(), Path(build)
    metadata = root / 'native-binaries.json'
    if not metadata.exists():
        # Older complete bundles remain usable on the original build's GPU.
        return {'schema': 1, 'cuda_architectures': ['120a-real'],
                'cuda_driver_min': 12080, 'legacy': True}
    try:
        manifest = json.loads(metadata.read_text('utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError('语音原生库清单无法读取，请重新下载最新更新包') from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
        raise RuntimeError('语音原生库清单无效，请重新下载最新更新包')
    entries = manifest['files']
    if any(not isinstance(item, dict) for item in entries):
        raise RuntimeError('语音原生库清单无效，请重新下载最新更新包')
    paths = [item.get('path') for item in entries]
    if (manifest.get('schema') != 1 or len(paths) != len(MEMBERS) or
            any(not isinstance(path, str) for path in paths) or
            set(paths) != MEMBERS or any(not isinstance(item.get('bytes'), int) or
            item['bytes'] <= 0 or not isinstance(item.get('sha256'), str) or
            re.fullmatch(r'[0-9a-f]{64}', item['sha256']) is None for item in entries) or
            not isinstance(manifest.get('cuda_driver_min'), int) or
            manifest['cuda_driver_min'] < 12000 or
            not isinstance(manifest.get('cuda_architectures'), list) or
            not manifest['cuda_architectures'] or
            any(not isinstance(arch, str) or not re.fullmatch(r'[0-9]{2,3}a?-(real|virtual)', arch)
                for arch in manifest['cuda_architectures'])):
        raise RuntimeError('语音原生库清单无效，请重新下载最新更新包')
    expected = (root / '.runtime/build-native-cu128').resolve()
    if build.resolve() != expected or not expected.is_relative_to(root):
        raise RuntimeError('语音原生库目标目录无效，不能替换其他项目的文件')
    if _installed(build, manifest):
        return manifest
    parts = manifest.get('parts', [])
    if not isinstance(parts, list) or not parts or len(parts) > 24:
        raise RuntimeError('语音原生库分片清单无效')
    sources, missing = [], False
    for index, part in enumerate(parts):
        name = f'native-binaries.{index:03d}.bin'
        if (not isinstance(part, dict) or part.get('path') != name or type(part.get('bytes')) is not int or
                not 0 < part['bytes'] <= 8 * 1024 * 1024 or
                not isinstance(part.get('sha256'), str) or
                re.fullmatch(r'[0-9a-f]{64}', part['sha256']) is None):
            raise RuntimeError('语音原生库分片路径或大小无效')
        source = root / name
        if source.resolve().parent != root:
            raise RuntimeError('语音原生库分片路径无效')
        if not source.exists():
            missing = True
        elif not source.is_file() or source.stat().st_size != part['bytes'] or _hash(source) != part['sha256']:
            # A partial offline bundle must not hide corruption by downloading.
            raise RuntimeError('语音原生库分片损坏，请重新下载完整整合包')
        sources.append(source)
    total = sum(part['bytes'] for part in parts)
    expected_bytes = manifest.get('archive_bytes', total if 'release_asset' not in manifest else None)
    if (type(expected_bytes) is not int or not 0 < expected_bytes <= MAX_PAYLOAD or expected_bytes != total or
            not isinstance(manifest.get('archive_sha256'), str) or
            re.fullmatch(r'[0-9a-f]{64}', manifest['archive_sha256']) is None):
        raise RuntimeError('语音原生库归档大小或校验清单无效')
    if missing:
        _release_url(manifest)  # Fail before creating any download/install files.
    temporary = backup = payload_path = None
    promoted = False
    try:
        build.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='native-payload-', suffix='.zip', dir=build.parent)
        os.close(fd)
        payload_path = Path(name)
        if missing:
            _download_archive(manifest, payload_path, expected_bytes)
        else:
            with payload_path.open('wb') as destination:
                for source in sources:
                    with source.open('rb') as part:
                        shutil.copyfileobj(part, destination, length=1024 * 1024)
            if payload_path.stat().st_size != expected_bytes or _hash(payload_path) != manifest['archive_sha256']:
                raise RuntimeError('语音原生库归档校验失败，请重新下载完整整合包')
        temporary = Path(tempfile.mkdtemp(prefix='native-install-', dir=build.parent))
        backup = temporary.with_name(temporary.name + '-previous')
        with zipfile.ZipFile(payload_path) as archive:
            infos = archive.infolist()
            if (len(infos) != len(MEMBERS) or {info.filename for info in infos} != MEMBERS or
                    sum(info.file_size for info in infos) > MAX_EXPANDED):
                raise RuntimeError('语音原生库归档文件列表无效')
            records = {item['path']: item for item in entries}
            for info in infos:
                name = PurePosixPath(info.filename)
                if (name.is_absolute() or '..' in name.parts or '\\' in info.filename or
                        ((info.external_attr >> 16) & 0o170000) not in (0, 0o100000) or
                        info.flag_bits & 1 or
                        info.file_size != records[info.filename]['bytes']):
                    raise RuntimeError('语音原生库归档包含无效文件')
                target = temporary.joinpath(*name.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('wb') as destination:
                    shutil.copyfileobj(source, destination)
        if not _installed(temporary, manifest):
            raise RuntimeError('语音原生库解压校验失败')
        had_build = build.exists()
        if had_build:
            os.replace(build, backup)
        try:
            os.replace(temporary, build)
            promoted = True
        except OSError:
            if had_build:
                try:
                    os.replace(backup, build)
                except OSError as exc:
                    raise RuntimeError(f'语音原生库更新和回滚受阻，旧库保留在 {backup}。请关闭整合包后重试。') from exc
            raise
        return manifest
    except (zipfile.BadZipFile, EOFError, zlib.error, NotImplementedError) as exc:
        raise RuntimeError('语音原生库 ZIP 归档损坏或格式不支持，请重试或重新下载完整整合包；旧库未被替换') from exc
    except OSError as exc:
        raise RuntimeError('语音原生库更新失败，请关闭整合包和视频语音翻译后重试') from exc
    finally:
        if payload_path is not None:
            try:
                payload_path.unlink(missing_ok=True)
            except OSError:
                pass  # Do not replace a successful install/failure cause with cleanup errors.
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        # A failed rollback must keep the only surviving previous library.
        if backup is not None and backup.exists() and promoted:
            shutil.rmtree(backup, ignore_errors=True)


def cuda_device_info() -> dict:
    """Read the CUDA driver without importing Torch or launching a kernel."""
    if os.name != 'nt':
        return {'available': False, 'reason': '当前系统不是 Windows'}
    visibility = os.environ.get('CUDA_VISIBLE_DEVICES', '').strip()
    if 'CUDA_VISIBLE_DEVICES' in os.environ and not visibility:
        return {'available': False, 'reason': 'CUDA_VISIBLE_DEVICES 已禁用显卡'}
    if visibility:
        if visibility == '-1':
            return {'available': False, 'reason': 'CUDA_VISIBLE_DEVICES 已禁用显卡'}
        selected = [value.strip() for value in visibility.split(',')]
        if not all(value.isdigit() for value in selected):
            return {'available': False, 'reason': '无法验证 CUDA_VISIBLE_DEVICES 指定的显卡'}
        ordinals = [int(value) for value in selected]
    else:
        ordinals = None
    try:
        dll = ctypes.WinDLL(str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                                'System32/nvcuda.dll'))
        def check(code):
            if code != 0:
                raise OSError(f'CUDA driver error {code}')
        check(dll.cuInit(0))
        count, device, major, minor, driver = [ctypes.c_int() for _ in range(5)]
        check(dll.cuDeviceGetCount(ctypes.byref(count)))
        if count.value < 1:
            return {'available': False, 'reason': '没有可用的 NVIDIA 显卡'}
        check(dll.cuDriverGetVersion(ctypes.byref(driver)))
        devices = []
        # llama.cpp may split layers across all visible GPUs.
        for ordinal in ordinals if ordinals is not None else range(count.value):
            check(dll.cuDeviceGet(ctypes.byref(device), ordinal))
            check(dll.cuDeviceGetAttribute(ctypes.byref(major), 75, device))
            check(dll.cuDeviceGetAttribute(ctypes.byref(minor), 76, device))
            name = ctypes.create_string_buffer(256)
            check(dll.cuDeviceGetName(name, len(name), device))
            devices.append({'major': major.value, 'minor': minor.value,
                            'name': name.value.decode('utf-8', errors='replace')})
        return {'available': True, **devices[0], 'driver': driver.value, 'devices': devices}
    except (OSError, AttributeError) as exc:
        return {'available': False, 'reason': f'NVIDIA 驱动不可用（{exc}）'}


def select_backend(manifest: dict, gpu_layers: int, device: dict | None = None) -> dict:
    if gpu_layers == 0:
        return {'backend': 'cpu', 'gpu_layers': 0, 'warning': '', 'requested': 0}
    device = cuda_device_info() if device is None else device
    def covered(capability):
        found = False
        for arch in manifest.get('cuda_architectures', []):
            value, kind = arch.rsplit('-', 1)
            if value.endswith('a'):
                found |= kind == 'real' and capability == int(value[:-1])
            elif kind == 'virtual':
                found |= capability >= int(value)
            else:
                found |= capability // 10 == int(value) // 10 and capability >= int(value)
        return found
    supported = bool(device.get('available') and
                     device.get('driver', 0) >= manifest.get('cuda_driver_min', 12080) and
                     all(covered(item.get('major', 0) * 10 + item.get('minor', 0))
                         for item in device.get('devices', [device])))
    if supported:
        return {'backend': 'cuda', 'gpu_layers': gpu_layers, 'warning': '',
                'requested': gpu_layers, 'device': device}
    reason = device.get('reason') or '显卡架构或 NVIDIA 驱动版本不符合此预编译库的要求'
    return {'backend': 'cpu', 'gpu_layers': 0, 'requested': gpu_layers,
            'device': device, 'warning': f'{reason}；已使用 CPU 语音识别，速度会降低。支持的 NVIDIA 显卡请更新驱动后重试。'}
