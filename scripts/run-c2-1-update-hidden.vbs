Option Explicit
Dim shell, fso, root, pythonPath, runner, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
If WScript.Arguments.Count > 0 Then
  pythonPath = WScript.Arguments(0)
Else
  pythonPath = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
  If Not fso.FileExists(pythonPath) Then pythonPath = "pythonw.exe"
End If
runner = root & "\scripts\run_c2_1_update.py"
command = Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & runner & Chr(34) & " --trigger automatic"
shell.Run command, 0, True
