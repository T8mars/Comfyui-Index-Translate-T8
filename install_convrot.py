"""Install official wheels into a reversible overlay using this host's Python."""
import os
import site
import subprocess
import sys
from pathlib import Path

if sys.platform != 'win32':
    raise SystemExit('Windows installer: other systems should install requirements-convrot.txt with their host Python.')
destination=Path(site.getsitepackages()[-1]) / 'index-translate-convrot-deps'
subprocess.run([sys.executable,'-m','pip','install','--no-deps','--only-binary=:all:',
               '--target',str(destination),'comfy-kitchen==0.2.37','nvidia-cublas==13.2.2.2'],check=True)
activation=destination.parent/'index_translate_convrot.pth'
activation.write_text("import sys,os,ctypes; sys.path.insert(0,os.path.join(sys.prefix,'Lib','site-packages','index-translate-convrot-deps')); ctypes.WinDLL(os.path.join(sys.prefix,'Lib','site-packages','index-translate-convrot-deps','nvidia','cu13','bin','x86_64','cublasLt64_13.dll'))\n",'utf-8')
print('ConvRot support installed. Restart ComfyUI to apply. Rollback: remove only '+str(activation))
