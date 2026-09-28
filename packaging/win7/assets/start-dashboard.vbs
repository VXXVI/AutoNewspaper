' Start the local dashboard without any console window.
'
' Double-clicking this file must not flash a black cmd window, so it runs
' pythonw.exe (the windowless interpreter shipped in python\\) instead of
' python.exe. run.py routes into newspaper_auto.exe's entry point, which copes
' with absent stdout/stderr (pythonw gives None).
'
' Keep this file pure ASCII: wscript.exe reads .vbs in the system ANSI codepage,
' and an ASCII-only source is immune to codepage surprises.
Option Explicit

Dim shell, fso, here
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here

' 0 = hidden window, False = do not wait for the process to finish.
shell.Run """" & here & "\python\pythonw.exe"" """ & here & "\run.py""", 0, False
