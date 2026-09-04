' TRPG prep launcher - starts start.bat with a hidden console window (no black box).
' Double-click start.vbs to launch; double-click stop.bat to stop.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
comspec = sh.ExpandEnvironmentStrings("%ComSpec%")
sh.Run """" & comspec & """ /d /c """ & dir & "\start.bat"" --no-pause", 0, False

' Bootstrap can take longer than a fixed delay. Open the browser only after
' the local server responds, or stop quietly after the timeout.
deadline = DateAdd("s", 180, Now)
ready = False
Do While Now < deadline
    If IsServerReady() Then
        ready = True
        Exit Do
    End If
    WScript.Sleep 500
Loop

If ready Then
    sh.Run "http://127.0.0.1:8000", 1, False
End If

Function IsServerReady()
    Dim request, status, useWinHttp
    IsServerReady = False
    useWinHttp = False
    On Error Resume Next
    Set request = CreateObject("WinHttp.WinHttpRequest.5.1")
    If Not request Is Nothing Then
        useWinHttp = True
    End If
    If request Is Nothing Then
        Err.Clear
        Set request = CreateObject("MSXML2.XMLHTTP.6.0")
    End If
    If request Is Nothing Then
        Err.Clear
        On Error GoTo 0
        Exit Function
    End If
    If useWinHttp Then
        request.SetTimeouts 500, 500, 500, 500
    End If
    request.Open "GET", "http://127.0.0.1:8000/", False
    request.Send
    status = request.Status
    If Err.Number = 0 Then
        IsServerReady = (status >= 200 And status < 500)
    End If
    Err.Clear
    On Error GoTo 0
End Function
