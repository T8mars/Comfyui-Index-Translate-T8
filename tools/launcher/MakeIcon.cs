using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
class MakeIcon {
    [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr h);
    static void Main(string[] args) {
        using(var bitmap=new Bitmap(64,64)) {
            using(var g=Graphics.FromImage(bitmap)) {
                g.Clear(Color.Transparent); g.SmoothingMode=SmoothingMode.AntiAlias;
                using(var path=new GraphicsPath()) {
                    path.AddArc(1,1,18,18,180,90); path.AddArc(45,1,18,18,270,90);
                    path.AddArc(45,45,18,18,0,90); path.AddArc(1,45,18,18,90,90); path.CloseFigure();
                    using(var brush=new SolidBrush(Color.FromArgb(74,167,171))) g.FillPath(brush,path);
                }
                using(var font=new Font("Microsoft YaHei UI",32F,FontStyle.Bold,GraphicsUnit.Pixel))
                using(var brush=new SolidBrush(Color.White))
                using(var format=new StringFormat{Alignment=StringAlignment.Center,LineAlignment=StringAlignment.Center})
                    g.DrawString("译",font,brush,new RectangleF(0,0,64,64),format);
            }
            IntPtr handle=bitmap.GetHicon();
            try { using(var icon=Icon.FromHandle(handle)) using(var stream=File.Create(args[0])) icon.Save(stream); }
            finally { DestroyIcon(handle); }
        }
    }
}
