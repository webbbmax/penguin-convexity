Option Explicit

Dim arguments, command, fileSystem, index, pythonExe, scheduler, shell, scriptDirectory

Set arguments = WScript.Arguments
If arguments.Count < 1 Then
  WScript.Quit 2
End If

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
scheduler = fileSystem.BuildPath(scriptDirectory, "run_c1_8_scheduler.py")
pythonExe = arguments.Item(0)

command = QuoteArgument(pythonExe)
If LCase(fileSystem.GetFileName(pythonExe)) = "py.exe" Then
  command = command & " -3"
End If
command = command & " " & QuoteArgument(scheduler)

For index = 1 To arguments.Count - 1
  command = command & " " & QuoteArgument(arguments.Item(index))
Next

WScript.Quit shell.Run(command, 0, True)

Function QuoteArgument(value)
  QuoteArgument = Chr(34) & CStr(value) & Chr(34)
End Function
