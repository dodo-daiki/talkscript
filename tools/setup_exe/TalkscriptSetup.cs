// talkscript-setup.exe: ダブルクリックで install.ps1 を実行する入口（Windows）。
//
// install.ps1 をこの exe の中に入れておき、一時フォルダに書き出して PowerShell で実行する。
// 確認（y/N）は install.ps1 と talkscript setup がこの窓で尋ねる。終わったら結果を見せてから閉じる。
// 引数はそのまま install.ps1 に渡す（例: talkscript-setup.exe -NoVoicevox、-Uninstall）。
// --wait-pid 1,2 だけはこの exe が受け取り、そのプログラムが終わるのを待ってから始める（画面からの更新）。
// 作り方: python tools/build_setup.py（Windows に入っている .NET Framework 4 の csc を使う）。
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;

[assembly: AssemblyTitle("talkscript のインストーラー")]
[assembly: AssemblyProduct("talkscript")]
[assembly: AssemblyDescription("発話と動作の台本ツール talkscript を入れる")]
[assembly: AssemblyCopyright("MIT License")]

static class TalkscriptSetup
{
    static int Main(string[] args)
    {
        try { Console.OutputEncoding = new UTF8Encoding(false); } catch (IOException) { }
        Console.Title = "talkscript のインストール";
        int code = 1;
        try
        {
            code = Run(args);
        }
        catch (Exception e)
        {
            Console.ForegroundColor = ConsoleColor.Yellow;
            Console.WriteLine("インストーラーを動かせません: " + e.Message);
            Console.ResetColor();
        }
        Console.WriteLine();
        bool uninstall = Array.Exists(args, a => a.Equals("-Uninstall", StringComparison.OrdinalIgnoreCase));
        if (code == 0 && !uninstall)   // メニューでアンインストールを選んだときは、ショートカットがもうないので尋ねない
        {
            string lnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "talkscript.lnk");
            if (File.Exists(lnk))
            {
                Console.WriteLine("終わりました。デスクトップかスタートメニューの talkscript のアイコンから開けます。");
                Console.Write("今すぐ talkscript を開きますか？ [y/N] ");
                string a = (Console.ReadLine() ?? "").Trim().ToLowerInvariant();
                if (a == "y" || a == "yes" || a == "はい")
                {
                    Process.Start(new ProcessStartInfo(lnk) { UseShellExecute = true });
                }
                return 0;
            }
            Console.WriteLine("終わりました。");
        }
        else
        {
            Console.WriteLine("途中で止まりました（上の表示を確かめてください）。もう一度このファイルを開けば、続きから入れられます。");
        }
        Console.Write("Enter で閉じます。");
        Console.ReadLine();
        return code;
    }

    // --wait-pid 1,2: 画面から更新を始めたとき、そのプログラムが終わるのを待つ（使用中のファイルは置き換えられないため）
    static string[] WaitForPids(string[] args)
    {
        System.Collections.Generic.List<string> rest = new System.Collections.Generic.List<string>();
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--wait-pid" && i + 1 < args.Length)
            {
                foreach (string s in args[++i].Split(','))
                {
                    int pid;
                    if (!int.TryParse(s.Trim(), out pid)) continue;
                    try
                    {
                        using (Process p = Process.GetProcessById(pid))
                        {
                            Console.WriteLine("talkscript の画面が終わるのを待っています…");
                            if (!p.WaitForExit(60000)) Console.WriteLine("（60 秒待っても終わらないので、続けます）");
                        }
                    }
                    catch (ArgumentException) { }   // もう終わっている
                    catch (InvalidOperationException) { }
                }
                continue;
            }
            rest.Add(args[i]);
        }
        return rest.ToArray();
    }

    static int Run(string[] args)
    {
        args = WaitForPids(args);
        Assembly asm = Assembly.GetExecutingAssembly();
        string dir = Path.Combine(Path.GetTempPath(), "talkscript-setup-" + Process.GetCurrentProcess().Id);
        Directory.CreateDirectory(dir);
        string ps1 = Path.Combine(dir, "install.ps1");
        try
        {
            using (Stream s = asm.GetManifestResourceStream("install.ps1"))
            {
                if (s == null) throw new InvalidOperationException("中に install.ps1 がない（作り方の誤り）");
                using (FileStream f = File.Create(ps1)) { s.CopyTo(f); }
            }
            StringBuilder cmd = new StringBuilder("-NoProfile -ExecutionPolicy Bypass -File ");
            cmd.Append(Quote(ps1));
            foreach (string a in args) { cmd.Append(' ').Append(Quote(a)); }
            string ps = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            ProcessStartInfo psi = new ProcessStartInfo(File.Exists(ps) ? ps : "powershell.exe", cmd.ToString());
            psi.UseShellExecute = false;   // この窓をそのまま使う（確認の y/N をここで答える）
            using (Process p = Process.Start(psi))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        finally
        {
            try { Directory.Delete(dir, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }

    static string Quote(string a)
    {
        if (a.Length > 0 && a.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0) return a;
        return "\"" + a.Replace("\\\"", "\\\\\"").Replace("\"", "\\\"") + "\"";
    }
}
