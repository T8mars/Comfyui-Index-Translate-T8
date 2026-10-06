using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace T8IndexTranslate {
    static class Program {
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern uint RegisterWindowMessage(string name);
        [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);
        [STAThread] static void Main(string[] args) {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory).TrimEnd('\\').ToUpperInvariant();
            string id;
            using (var sha = SHA256.Create()) id = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root))).Replace("-", "");
            uint activate = RegisterWindowMessage("T8IndexTranslate.Activate." + id);
            bool first;
            using (var mutex = new Mutex(true, "Local\\T8IndexTranslate." + id, out first)) {
                if (!first) { PostMessage(new IntPtr(0xffff), activate, IntPtr.Zero, IntPtr.Zero); return; }
                try { Application.Run(new LauncherForm(args, activate)); }
                catch (Exception error) { MessageBox.Show(error.Message,"T8 Index Translate · By T8star",MessageBoxButtons.OK,MessageBoxIcon.Error); }
                finally { mutex.ReleaseMutex(); }
            }
        }
    }

    sealed class CommandResult {
        public int ExitCode;
        public string Output;
        public string Error;
    }

    sealed class LauncherForm : Form {
        readonly string root = AppDomain.CurrentDomain.BaseDirectory;
        readonly uint activateMessage;
        readonly Label state = new Label();
        readonly Label address = new Label();
        readonly Button open = new Button();
        readonly Button exit = new Button();
        readonly Button retry = new Button();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly object processGate = new object();
        Task startup;
        volatile bool closing;
        bool canClose, polling, autoBrowser = true;
        int port = 8098;
        string url;
        Process serviceProcess;
        FileStream desktopLock;

        public LauncherForm(string[] args, uint activate) {
            activateMessage = activate;
            for (int i=0; i<args.Length; ++i) {
                if (args[i] == "--no-browser") autoBrowser = false;
                else if (args[i] == "--port" && i+1<args.Length) {
                    if (!int.TryParse(args[++i], out port) || port<1024 || port>65535) throw new ArgumentException("无效服务端口");
                } else throw new ArgumentException("不支持的启动参数：" + args[i]);
            }
            Text = "T8 Index Translate · By T8star";
            Font = new Font("Microsoft YaHei UI", 10F);
            BackColor = Color.FromArgb(247,249,252);
            ClientSize = new Size(590,310);
            MinimumSize = new Size(606,349);
            MaximumSize = new Size(900,500);
            StartPosition = FormStartPosition.CenterScreen;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            ShowInTaskbar = true;
            var title = new Label {Text="T8 Index Translate",Font=new Font(Font.FontFamily,19F,FontStyle.Bold),
                ForeColor=Color.FromArgb(28,41,65),Location=new Point(24,20),Size=new Size(535,44)};
            var by = new Label {Text="By T8star · 本地网页与视频翻译 · " + new Version(Application.ProductVersion).ToString(3),ForeColor=Color.FromArgb(205,61,131),
                Location=new Point(26,67),Size=new Size(520,25)};
            state.Text="正在启动本地服务…"; state.Location=new Point(26,110); state.Size=new Size(530,68);
            state.ForeColor=Color.FromArgb(67,81,105);
            address.Location=new Point(26,181); address.Size=new Size(530,25);
            open.Text="打开翻译界面"; open.Enabled=false; open.Location=new Point(26,222); open.Size=new Size(163,43);
            open.FlatStyle=FlatStyle.Flat; open.BackColor=Color.FromArgb(224,84,151); open.ForeColor=Color.White;
            retry.Text="重试启动"; retry.Visible=false; retry.Location=new Point(201,222); retry.Size=new Size(140,43);
            exit.Text="停止服务并退出"; exit.Location=new Point(354,222); exit.Size=new Size(203,43);
            var note = new Label {Text="最小化后保留在任务栏；关闭窗口会释放模型并关闭本包服务。",
                ForeColor=Color.FromArgb(116,128,151),Location=new Point(26,279),Size=new Size(545,24),Font=new Font(Font.FontFamily,9F)};
            Controls.AddRange(new Control[]{title,by,state,address,open,retry,exit,note});
            open.Click += delegate { OpenBrowser(); };
            exit.Click += delegate { Close(); };
            retry.Click += delegate { BeginStartup(); };
            Shown += delegate { BeginStartup(); };
            FormClosing += OnClosing;
            Directory.CreateDirectory(Path.Combine(root,"data"));
            desktopLock = new FileStream(Path.Combine(root,"data","desktop.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.ReadWrite);
            if (desktopLock.Length == 0) { desktopLock.WriteByte(48); desktopLock.Flush(); }
            desktopLock.Lock(0,1);
            FormClosed += delegate { timer.Dispose(); desktopLock.Dispose(); if (serviceProcess != null) serviceProcess.Dispose(); };
            timer.Interval=5000; timer.Tick += delegate { Poll(); };
        }

        protected override void WndProc(ref Message m) {
            if ((uint)m.Msg == activateMessage) {
                if (WindowState == FormWindowState.Minimized) WindowState=FormWindowState.Normal;
                Show(); Activate(); if (!closing && autoBrowser) OpenBrowser();
            }
            base.WndProc(ref m);
        }
        void UpdateUI(Action action) {
            if (!IsDisposed && IsHandleCreated) {
                try { BeginInvoke(action); } catch (InvalidOperationException) {}
            }
        }
        CommandResult Python(string script, string arguments) {
            string python=Path.Combine(root,"runtime","python.exe");
            if (!File.Exists(python) || !File.Exists(Path.Combine(root,script))) throw new FileNotFoundException("缺少私有 Python 或启动文件，请使用完整整合包。");
            var info=new ProcessStartInfo(python, "-E -s -X utf8 \"" + Path.Combine(root,script) + "\" " + arguments) {
                WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,
                RedirectStandardOutput=true,RedirectStandardError=true,
                StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8
            };
            info.EnvironmentVariables["PYTHONUTF8"]="1";
            using (var process=Process.Start(info)) {
                var output=Task.Factory.StartNew(()=>process.StandardOutput.ReadToEnd());
                var error=Task.Factory.StartNew(()=>process.StandardError.ReadToEnd());
                process.WaitForExit();
                return new CommandResult {ExitCode=process.ExitCode,Output=output.Result.Trim(),Error=error.Result.Trim()};
            }
        }
        Dictionary<string,object> Status() {
            var result=Python("launcher.py","status --json");
            if (result.ExitCode != 0) throw new Exception(result.Error.Length>0 ? result.Error : result.Output);
            return new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(result.Output);
        }
        void BeginStartup() {
            if (closing || (startup != null && !startup.IsCompleted)) return;
            retry.Visible=false; open.Enabled=false;
            state.Text="正在检查更新并启动本地服务…";
            startup=Task.Factory.StartNew(()=> {
                lock (processGate) {
                    try {
                        // CMD can apply updates before opening the EXE. The desktop lock
                        // defers replacement of a running EXE; auto checks remain cached.
                        Python("update.py","auto");
                        if (closing) return;
                        var result=Python("launcher.py","start --no-browser --port " + port);
                        if (result.ExitCode != 0) throw new Exception(result.Error.Length>0 ? result.Error : result.Output);
                        var info=Status();
                        if (!(bool)info["owned"] || !(bool)info["ready"]) throw new Exception("本地服务尚未就绪，请查看 data/service.log。");
                        url=(string)info["url"];
                        serviceProcess=Process.GetProcessById(Convert.ToInt32(info["pid"]));
                        UpdateUI(()=> {
                            if (closing) return;
                            state.Text="服务已启动。\r\n请在浏览器中加载模型或开始翻译；退出时关闭本窗口。";
                            address.Text=url; open.Enabled=true; timer.Start();
                            if (autoBrowser) OpenBrowser();
                        });
                    } catch (Exception error) {
                        UpdateUI(()=> { if (!closing) { state.Text="启动失败：" + error.Message; retry.Visible=true; } });
                    }
                }
            });
        }
        void OpenBrowser() {
            if (String.IsNullOrEmpty(url) || closing) return;
            try { Process.Start(new ProcessStartInfo(url) {UseShellExecute=true}); }
            catch (Exception error) { state.Text="服务已启动，但浏览器未打开：" + error.Message; }
        }
        void Poll() {
            if (polling || closing) return;
            polling=true;
            Task.Factory.StartNew(()=> {
                try {
                    var info=Status();
                    UpdateUI(()=> {
                        if (closing) return;
                        bool ready=(bool)info["ready"];
                        open.Enabled=ready;
                        if (!ready) { state.Text=(bool)info["owned"] ? "服务正在清理或启动，请稍候…" : "本包服务已停止；可重试启动，或关闭窗口。"; retry.Visible=!(bool)info["owned"]; }
                    });
                } catch (Exception error) { UpdateUI(()=> { if (!closing) state.Text="服务状态暂时无法确认：" + error.Message; }); }
                finally { polling=false; }
            });
        }
        void OnClosing(object sender, FormClosingEventArgs e) {
            if (canClose) return;
            e.Cancel=true;
            if (closing) return;
            closing=true; timer.Stop(); open.Enabled=false; retry.Visible=false; exit.Enabled=false;
            state.Text="正在取消任务、释放模型并停止服务…\r\n清理完成后此窗口会自动关闭。";
            Task.Factory.StartNew(()=> {
                try {
                        // Waiting for startup prevents a close/start race from reviving the service.
                        if (startup != null) startup.Wait();
                    lock (processGate) {
                        while (true) {
                            var result=Python("launcher.py","stop");
                            var info=Status();
                            if (!(bool)info["owned"]) {
                                if (serviceProcess != null) serviceProcess.WaitForExit();
                                break;
                            }
                            UpdateUI(()=>state.Text="取消请求已提交，正在等待模型或语音任务清理…\r\n编译中的步骤结束后会自动退出，请保留此窗口。");
                            Thread.Sleep(1000);
                        }
                    }
                    UpdateUI(()=> { canClose=true; Close(); });
                } catch (Exception error) {
                    UpdateUI(()=> { state.Text="尚未确认服务停止：" + error.Message + "\r\n请检查完整包文件后再点“停止服务并退出”。"; closing=false; exit.Enabled=true; });
                }
            });
        }
    }
}
