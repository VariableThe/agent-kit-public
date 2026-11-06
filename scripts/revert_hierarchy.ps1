<#
.SYNOPSIS
  Undo the agent-kit hierarchy setup on Windows and restore a plain plan/build opencode config.

.DESCRIPTION
  Removes the hierarchy plugin setup and reverts to normal plan/build:
    - hierarchy agents (monarch, bishop, knight, mercenary, witch, mage,
      grand-bishop, knight-commander) are dropped from the config
    - the build and plan overrides are removed, so opencode's built-in
      plan/build agents are used again
    - the hierarchy-guard plugin folder and the installer symlinks/junctions
      are removed
    - a project-local opencode.json is cleaned too when -Project is given

  It does not touch provider blocks, models or credentials: only agent entries
  and the symlinks this repo's installer creates.

  Every file it rewrites is backed up first: <file>.bak-<timestamp>-pre-revert

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\revert_hierarchy.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\revert_hierarchy.ps1 -Project C:\work\my-app
#>
[CmdletBinding()]
param(
  # Home directory that holds .config\opencode. Defaults to the user profile.
  [string]$UserHome = $(if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }),

  # Optional project directory whose opencode.json should be cleaned as well.
  [string]$Project = "",

  # Report what would change without touching anything.
  [switch]$DryRun
)

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Stop"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$onWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT

$configDir  = Join-Path $UserHome ".config/opencode"
$configFile = Join-Path $configDir "opencode.json"
$pluginDir  = Join-Path $configDir "plugins"

$hierarchyAgents = @(
  "monarch", "bishop", "knight", "mercenary",
  "witch", "mage", "grand-bishop", "knight-commander"
)

function Write-Step([string]$message) { Write-Host $message }

function Test-IsLink([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) { return $false }
  $item = Get-Item -LiteralPath $path -Force
  if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $true }
  return $false
}

function Remove-LinkOrFolder([string]$path, [string]$label) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  if (Test-IsLink $path) {
    # A symlink or junction: remove the link itself, never the target's contents.
    $item = Get-Item -LiteralPath $path -Force
    if ($DryRun) { Write-Step "would remove $label : $path"; return }
    if ($onWindows) {
      if ($item.PSIsContainer) {
        # cmd rmdir removes a directory junction or symlink without walking into it.
        cmd /c rmdir "$path"
        if (Test-Path -LiteralPath $path) {
          [System.IO.Directory]::Delete($path, $false)
        }
      } else {
        [System.IO.File]::Delete($path)
      }
    } else {
      Remove-Item -LiteralPath $path -Force
    }
    if (Test-Path -LiteralPath $path) {
      Write-Step "could not remove $label : $path"
    } else {
      Write-Step "removed $label : $path"
    }
    return
  }
  if ($DryRun) { Write-Step "would remove $label : $path"; return }
  Remove-Item -LiteralPath $path -Recurse -Force
  Write-Step "removed $label : $path"
}

function Clean-OpencodeConfig([string]$file) {
  if (-not (Test-Path -LiteralPath $file)) { return }
  $backup = "$file.bak-$timestamp-pre-revert"
  if (-not $DryRun) {
    Copy-Item -LiteralPath $file -Destination $backup -Force
    Write-Step "backup : $backup"
  }

  $raw = Get-Content -LiteralPath $file -Raw
  try {
    $config = $raw | ConvertFrom-Json
  } catch {
    Write-Step "skipped $file (not valid JSON)"
    return
  }

  foreach ($bagName in @("agents", "agent")) {
    $bag = $config.$bagName
    if ($null -eq $bag) { continue }
    foreach ($name in $hierarchyAgents) {
      if ($bag.PSObject.Properties.Name -contains $name) {
        $bag.PSObject.Properties.Remove($name)
        Write-Step "removed agent '$name' from '$bagName'"
      }
    }
    foreach ($name in @("build", "plan")) {
      if ($bag.PSObject.Properties.Name -contains $name) {
        $bag.PSObject.Properties.Remove($name)
        Write-Step "removed '$bagName.$name' override (opencode built-in plan/build applies again)"
      }
    }
    if (@($bag.PSObject.Properties).Count -eq 0) {
      $config.PSObject.Properties.Remove($bagName)
    }
  }

  if ($DryRun) { Write-Step "would rewrite : $file"; return }
  $json = $config | ConvertTo-Json -Depth 100
  # UTF-8 without BOM: JSON.parse on the opencode side chokes on a BOM.
  [System.IO.File]::WriteAllText($file, $json, (New-Object System.Text.UTF8Encoding $false))
  Write-Step "rewrote : $file"
}

try {
  Write-Step "== agent-kit hierarchy revert =="
  if (Test-Path -LiteralPath $configFile) {
    Clean-OpencodeConfig $configFile
  } else {
    Write-Step "no opencode config at $configFile (nothing to clean)"
  }

  Remove-LinkOrFolder (Join-Path $pluginDir "hierarchy-guard") "hierarchy-guard plugin"

  foreach ($link in @(
    (Join-Path $configDir "AGENTS.md"),
    (Join-Path $configDir "cli.json"),
    (Join-Path $UserHome ".local/bin/beeper-mcp-shim"),
    (Join-Path $UserHome ".local/bin/tailshare"),
    (Join-Path $pluginDir "tailshare")
  )) {
    if (Test-IsLink $link) { Remove-LinkOrFolder $link "installer link" }
  }

  foreach ($skillsDir in @((Join-Path $configDir "skills"), (Join-Path $UserHome ".agents/skills"))) {
    if (-not (Test-Path -LiteralPath $skillsDir)) { continue }
    foreach ($skill in Get-ChildItem -LiteralPath $skillsDir -Force -ErrorAction SilentlyContinue) {
      if (-not (Test-IsLink $skill.FullName)) { continue }
      $target = ($skill.Target -join " ")
      # Only symlinks pointing into an agent-kit checkout, identified by its own
      # installer files. A lookalike path (my-agent-kit-notes/skills/...) is left
      # alone rather than matched by name.
      $idx = $target.LastIndexOf("/skills/")
      if ($idx -lt 0) { $idx = $target.LastIndexOf("\skills\") }
      if ($idx -lt 0) { continue }
      $repoRoot = $target.Substring(0, $idx)
      $hasInstaller = Test-Path -LiteralPath (Join-Path $repoRoot "install.sh")
      $hasRules = Test-Path -LiteralPath (Join-Path $repoRoot "AGENTS.md")
      $isCheckout = $hasInstaller -and $hasRules
      if ($isCheckout) {
        if ($DryRun) { Write-Step "would remove skill link : $($skill.FullName)"; continue }
        Remove-LinkOrFolder $skill.FullName "skill link"
      }
    }
  }

  if ($Project) {
    foreach ($file in @((Join-Path $Project "opencode.json"), (Join-Path $Project "opencode.base.json"))) {
      if (Test-Path -LiteralPath $file) { Clean-OpencodeConfig $file }
    }
  }

  Write-Host ""
  Write-Step "done. Restart opencode."
  Write-Step "  - build and plan are plain opencode agents again (no hierarchy routing)"
  Write-Step "  - hierarchy-guard plugin and installer links are gone"
  Write-Step "  - backups are *.bak-$timestamp-pre-revert"
  Write-Step "  - to delete the clone as well: delete the agent-kit folder you cloned"
}
finally {
  $ErrorActionPreference = $previousErrorActionPreference
}
