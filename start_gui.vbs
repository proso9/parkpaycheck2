'  parkcheck 图形界面静默启动脚本（无命令行黑窗口）
' 用法：双击本文件即可后台启动 check_gui.py；
'       开机自启：Win+R 输入 shell:startup 回车，把本文件的快捷方式放进去。
Option Explicit

Dim shell, fso, pythonw, candidates, root, i

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

candidates = Array( _
    "C:\Python313\pythonw.exe", "C:\Python312\pythonw.exe", "C:\Python311\pythonw.exe", _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe", _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe", _
    shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe")

pythonw = "pythonw"
For i = 0 To UBound(candidates)
    If fso.FileExists(candidates(i)) Then
        pythonw = candidates(i)
        Exit For
    End If
Next

shell.Run """" & pythonw & """ """ & root & "\check_gui.py""", 0, False
