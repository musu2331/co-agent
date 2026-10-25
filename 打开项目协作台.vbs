Option Explicit
Dim shell, fs, folder, args
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
args = " -B " & Chr(34) & folder & "\webui.py" & Chr(34) & " --launch"
On Error Resume Next
shell.Run "pyw -3" & args, 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "pythonw" & args, 0, False
End If
If Err.Number <> 0 Then
    MsgBox "Install Python 3.12 or newer with its launcher or PATH option, then try again.", 16, "Project Bridge"
End If
