using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class DashboardLauncher
{
    [STAThread]
    private static int Main()
    {
        try
        {
            string system = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "sistem");
            if (!File.Exists(Path.Combine(system, "panel", "dashboard.py")))
                throw new FileNotFoundException("Letakkan Dashboard Bot.exe di samping MENU.bat.");

            var start = new ProcessStartInfo("python.exe", "-m panel.dashboard")
            {
                WorkingDirectory = system,
                CreateNoWindow = true,
                UseShellExecute = false,
                RedirectStandardError = true
            };
            using (Process process = Process.Start(start))
            {
                string error = process.StandardError.ReadToEnd();
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new InvalidOperationException(error.Trim());
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Dashboard Bot gagal dibuka",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
