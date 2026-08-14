Option Explicit
Dim shell, fso, root, pythonPath, maintenance, maintenanceCommand, runner, command, runnerExitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
If WScript.Arguments.Count > 0 Then
  pythonPath = WScript.Arguments(0)
Else
  pythonPath = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
  If Not fso.FileExists(pythonPath) Then pythonPath = "pythonw.exe"
End If
maintenance = root & "\scripts\temp_artifact_retention.py"
maintenanceCommand = Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & maintenance & Chr(34) & " sweep --min-interval-hours 24"
shell.Run maintenanceCommand, 0, True
runner = root & "\scripts\run_c2_2_update.py"
command = Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & runner & Chr(34) & " --job due --trigger automatic"
runnerExitCode = shell.Run(command, 0, True)
WScript.Quit runnerExitCode
