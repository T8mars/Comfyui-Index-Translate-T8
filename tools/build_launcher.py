"""Build the small native Windows launcher using the installed .NET compiler."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# CMD's UTF-8 parser needs Windows line endings, especially around Chinese echo.
cmd = ROOT/'standalone/windows/start.cmd'
cmd.write_bytes(('\r\n'.join(cmd.read_text('utf-8').splitlines())+'\r\n').encode('utf-8'))
BUILD = ROOT/'.build/launcher'
BUILD.mkdir(parents=True,exist_ok=True)
compiler = Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if not compiler.is_file():
    raise RuntimeError('需要 Windows .NET Framework 4 编译器')
def compile_to(output, source, *extra):
    subprocess.run([str(compiler),'/nologo','/optimize+','/utf8output','/out:'+str(output),*extra,str(source)],check=True)
icon_tool = BUILD/'MakeIcon.exe'
icon = BUILD/'launcher.ico'
compile_to(icon_tool,ROOT/'tools/launcher/MakeIcon.cs','/r:System.Drawing.dll')
subprocess.run([str(icon_tool),str(icon)],check=True)
version = (ROOT/'standalone/windows/VERSION').read_text('utf-8').strip()
assembly = BUILD/'Version.cs'
assembly.write_text('using System.Reflection;\n[assembly: AssemblyTitle("T8 Index Translate")][assembly: AssemblyCompany("By T8star")][assembly: AssemblyProduct("T8 Index Translate")][assembly: AssemblyVersion("'+version+'.0")][assembly: AssemblyFileVersion("'+version+'.0")]\n','utf-8')
binary = BUILD/'T8IndexTranslate.exe'
compile_to(binary,ROOT/'tools/launcher/Launcher.cs','/target:winexe',
           '/r:System.Windows.Forms.dll','/r:System.Drawing.dll','/r:System.Web.Extensions.dll',
           '/win32icon:'+str(icon),str(assembly))
target = ROOT/'standalone/windows/T8IndexTranslate.exe'
temporary = target.with_suffix('.exe.tmp')
temporary.write_bytes(binary.read_bytes())
temporary.replace(target)
print(json.dumps({'exe':str(target),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'version':version}))
