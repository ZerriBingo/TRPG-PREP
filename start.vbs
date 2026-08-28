' TRPG prep launcher - starts start.bat with a hidden console window (no black box).
' Double-click start.vbs to launch; double-click stop.bat to stop.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c """ & dir & "\start.bat"" --no-pause", 0, False
WScript.Sleep 2000
sh.Run "http://127.0.0.1:8000", 1, False
