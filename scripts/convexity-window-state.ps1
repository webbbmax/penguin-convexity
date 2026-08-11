if (-not ("PenguinWindowNative" -as [type])) {
  Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

public sealed class PenguinWindowInfo
{
    public long Handle { get; set; }
    public int ProcessId { get; set; }
    public string Title { get; set; }
}

public sealed class PenguinWindowPlacementData
{
    public int Left { get; set; }
    public int Top { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public bool Maximized { get; set; }
    public bool Minimized { get; set; }
}

public static class PenguinWindowNative
{
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WINDOWPLACEMENT
    {
        public int Length;
        public int Flags;
        public int ShowCmd;
        public int MinPositionX;
        public int MinPositionY;
        public int MaxPositionX;
        public int MaxPositionY;
        public RECT NormalPosition;
    }

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    private static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT placement);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr hWndInsertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags
    );

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool PostMessage(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    public static void EnableDpiAwareness()
    {
        try { SetProcessDPIAware(); } catch { }
    }

    public static PenguinWindowInfo[] GetVisibleEdgeWindows()
    {
        var windows = new List<PenguinWindowInfo>();
        EnumWindows(delegate (IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd))
            {
                return true;
            }

            uint processId;
            GetWindowThreadProcessId(hWnd, out processId);
            try
            {
                using (var process = Process.GetProcessById((int)processId))
                {
                    if (!string.Equals(process.ProcessName, "msedge", StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
            }
            catch
            {
                return true;
            }

            var length = GetWindowTextLength(hWnd);
            if (length <= 0)
            {
                return true;
            }

            var title = new StringBuilder(length + 1);
            GetWindowText(hWnd, title, title.Capacity);
            windows.Add(new PenguinWindowInfo
            {
                Handle = hWnd.ToInt64(),
                ProcessId = (int)processId,
                Title = title.ToString()
            });
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    public static PenguinWindowPlacementData ReadPlacement(long handle)
    {
        var placement = new WINDOWPLACEMENT();
        placement.Length = Marshal.SizeOf(typeof(WINDOWPLACEMENT));
        if (!GetWindowPlacement(new IntPtr(handle), ref placement))
        {
            throw new InvalidOperationException("Could not read the application window placement.");
        }

        return new PenguinWindowPlacementData
        {
            Left = placement.NormalPosition.Left,
            Top = placement.NormalPosition.Top,
            Width = placement.NormalPosition.Right - placement.NormalPosition.Left,
            Height = placement.NormalPosition.Bottom - placement.NormalPosition.Top,
            Maximized = placement.ShowCmd == 3,
            Minimized = placement.ShowCmd == 2 || placement.ShowCmd == 6 ||
                        placement.ShowCmd == 7 || placement.ShowCmd == 11
        };
    }

    public static bool RestorePlacement(
        long handle,
        int left,
        int top,
        int width,
        int height,
        bool maximized
    )
    {
        var hWnd = new IntPtr(handle);
        ShowWindow(hWnd, 9);
        var moved = SetWindowPos(hWnd, IntPtr.Zero, left, top, width, height, 0x0014);
        if (maximized)
        {
            ShowWindow(hWnd, 3);
        }
        return moved;
    }

    public static bool ActivateWindow(long handle)
    {
        var hWnd = new IntPtr(handle);
        ShowWindow(hWnd, 9);
        BringWindowToTop(hWnd);
        return SetForegroundWindow(hWnd);
    }

    public static bool CloseWindow(long handle)
    {
        return PostMessage(new IntPtr(handle), 0x0010, IntPtr.Zero, IntPtr.Zero);
    }
}
"@
}

[PenguinWindowNative]::EnableDpiAwareness()

function Read-PenguinWindowState {
  param([Parameter(Mandatory = $true)][string]$StatePath)

  if (-not (Test-Path -LiteralPath $StatePath)) {
    return $null
  }

  try {
    $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $left = [int]$state.left
    $top = [int]$state.top
    $width = [int]$state.width
    $height = [int]$state.height
    if ($width -lt 480 -or $height -lt 360) {
      return $null
    }

    return [PSCustomObject]@{
      Left = $left
      Top = $top
      Width = $width
      Height = $height
      Maximized = [bool]$state.maximized
    }
  } catch {
    return $null
  }
}

function Save-PenguinWindowState {
  param(
    [Parameter(Mandatory = $true)][string]$StatePath,
    [Parameter(Mandatory = $true)]$State
  )

  $stateDirectory = Split-Path -Parent $StatePath
  if (-not (Test-Path -LiteralPath $stateDirectory)) {
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
  }

  $payload = [ordered]@{
    version = 1
    left = [int]$State.Left
    top = [int]$State.Top
    width = [int]$State.Width
    height = [int]$State.Height
    maximized = [bool]$State.Maximized
    savedAt = (Get-Date).ToString("o")
  }
  $temporaryPath = "$StatePath.tmp"
  $payload | ConvertTo-Json | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
  Move-Item -LiteralPath $temporaryPath -Destination $StatePath -Force
}

function Resolve-PenguinWindowState {
  param($State)

  if ($null -eq $State) {
    return $null
  }

  Add-Type -AssemblyName System.Windows.Forms
  $screens = @([System.Windows.Forms.Screen]::AllScreens)
  if ($screens.Count -eq 0) {
    return $State
  }

  $width = [Math]::Max(480, [int]$State.Width)
  $height = [Math]::Max(360, [int]$State.Height)
  $bestScreen = $null
  $bestArea = 0
  foreach ($screen in $screens) {
    $area = $screen.WorkingArea
    $intersectionWidth = [Math]::Max(0, [Math]::Min(([int]$State.Left + $width), $area.Right) - [Math]::Max([int]$State.Left, $area.Left))
    $intersectionHeight = [Math]::Max(0, [Math]::Min(([int]$State.Top + $height), $area.Bottom) - [Math]::Max([int]$State.Top, $area.Top))
    $intersectionArea = $intersectionWidth * $intersectionHeight
    if ($intersectionArea -gt $bestArea) {
      $bestArea = $intersectionArea
      $bestScreen = $screen
    }
  }

  if ($null -eq $bestScreen -or $bestArea -lt 12800) {
    $bestScreen = $screens | Where-Object { $_.Primary } | Select-Object -First 1
    if ($null -eq $bestScreen) {
      $bestScreen = $screens[0]
    }
  }

  $workingArea = $bestScreen.WorkingArea
  $width = [Math]::Min($width, $workingArea.Width)
  $height = [Math]::Min($height, $workingArea.Height)
  $left = [Math]::Min([Math]::Max([int]$State.Left, $workingArea.Left), $workingArea.Right - $width)
  $top = [Math]::Min([Math]::Max([int]$State.Top, $workingArea.Top), $workingArea.Bottom - $height)

  if ($bestArea -lt 12800) {
    $left = $workingArea.Left + [Math]::Floor(($workingArea.Width - $width) / 2)
    $top = $workingArea.Top + [Math]::Floor(($workingArea.Height - $height) / 2)
  }

  return [PSCustomObject]@{
    Left = [int]$left
    Top = [int]$top
    Width = [int]$width
    Height = [int]$height
    Maximized = [bool]$State.Maximized
  }
}

function Get-PenguinWindowLaunchArguments {
  param(
    [Parameter(Mandatory = $true)][string]$AppUrl,
    $State
  )

  "--app=$AppUrl"
  if ($null -eq $State) {
    "--start-maximized"
    return
  }

  "--window-position=$($State.Left),$($State.Top)"
  "--window-size=$($State.Width),$($State.Height)"
  if ($State.Maximized) {
    "--start-maximized"
  }
}

function Get-PenguinEdgeWindowHandles {
  @([PenguinWindowNative]::GetVisibleEdgeWindows() | ForEach-Object { [long]$_.Handle })
}

function Get-PenguinAppWindows {
  $appTitle = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027)"
  @(
    [PenguinWindowNative]::GetVisibleEdgeWindows() |
      Where-Object { $_.Title -like "*$appTitle*" }
  )
}

function Wait-PenguinAppWindow {
  param([int]$TimeoutSeconds = 20)

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $window = Get-PenguinAppWindows | Select-Object -First 1
    if ($null -ne $window) {
      return $window
    }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)
  return $null
}

function Activate-PenguinAppWindow {
  param([Parameter(Mandatory = $true)][long]$Handle)

  [PenguinWindowNative]::ActivateWindow($Handle) | Out-Null
}

function Close-PenguinAppWindow {
  param([Parameter(Mandatory = $true)][long]$Handle)

  [PenguinWindowNative]::CloseWindow($Handle) | Out-Null
}

function Consolidate-PenguinAppWindows {
  $windows = @(Get-PenguinAppWindows)
  if (-not $windows.Count) {
    return $null
  }

  $primary = $windows | Select-Object -First 1
  $windows |
    Select-Object -Skip 1 |
    ForEach-Object { Close-PenguinAppWindow -Handle $_.Handle }
  Activate-PenguinAppWindow -Handle $primary.Handle
  return $primary
}

function Find-PenguinAppWindow {
  param(
    [long[]]$BeforeHandles = @(),
    [int]$TimeoutSeconds = 20
  )

  $knownHandles = @{}
  foreach ($handle in $BeforeHandles) {
    $knownHandles[[string]$handle] = $true
  }

  $appTitle = "$([char]0x4F01)$([char]0x9E45)$([char]0x6295)$([char]0x7814)-$([char]0x51F8)$([char]0x6027)"
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $titleMatch = $null
  do {
    $windows = @([PenguinWindowNative]::GetVisibleEdgeWindows())
    $newWindow = $windows |
      Where-Object { -not $knownHandles.ContainsKey([string]$_.Handle) } |
      Select-Object -First 1
    if ($null -ne $newWindow) {
      return $newWindow
    }

    $titleMatch = $windows |
      Where-Object { $_.Title -like "*$appTitle*" } |
      Select-Object -First 1
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)

  return $titleMatch
}

function Restore-PenguinAppWindow {
  param(
    [Parameter(Mandatory = $true)][long]$Handle,
    $State
  )

  if ($null -eq $State) {
    return
  }

  [PenguinWindowNative]::RestorePlacement(
    $Handle,
    [int]$State.Left,
    [int]$State.Top,
    [int]$State.Width,
    [int]$State.Height,
    [bool]$State.Maximized
  ) | Out-Null
}

function Watch-PenguinAppWindow {
  param(
    [Parameter(Mandatory = $true)][long]$Handle,
    [Parameter(Mandatory = $true)][string]$StatePath
  )

  $lastSignature = ""
  while ([PenguinWindowNative]::IsWindow([IntPtr]$Handle)) {
    try {
      $placement = [PenguinWindowNative]::ReadPlacement($Handle)
      if (-not $placement.Minimized -and $placement.Width -ge 480 -and $placement.Height -ge 360) {
        $signature = "$($placement.Left),$($placement.Top),$($placement.Width),$($placement.Height),$($placement.Maximized)"
        if ($signature -ne $lastSignature) {
          Save-PenguinWindowState -StatePath $StatePath -State $placement
          $lastSignature = $signature
        }
      }
    } catch {
      break
    }
    Start-Sleep -Milliseconds 750
  }
}
