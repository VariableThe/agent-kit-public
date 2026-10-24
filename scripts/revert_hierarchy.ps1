<#
.SYNOPSIS
  Undo the agent-kit hierarchy setup on Windows and restore a plain plan/build opencode config.

.DESCRIPTION
  Removes the hierarchy plugin setup and reverts to normal plan/build:
    - hierarchy agents (monarch, bishop, knight, mercenary, p8-worker, witch,
      mage, grand-bishop, knight-commander) are dropped from the config
    - the build and plan overrides are removed, so opencode's built-in
      plan/build agents are used again
    - the p8-agent / p8-agent-fast providers and the pinned p8 model are
      dropped when the private p8 key file is missing (that provider block is
      what usually leaves a fresh public-repo install unable to start)
    - the hierarchy-guard plugin folder and the installer symlinks/junctions
      are removed
    - a project-local opencode.json is cleaned too when -Project is given

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
$p8KeyFile  = Join-Path $configDir "secrets/p8-api-key"

$hierarchyAgents = @(
  "monarch", "bishop", "knight", "mercenary", "p8-worker",
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

  # The p8 providers need a private key file; without it opencode cannot start.
  if (-not (Test-Path -LiteralPath $p8KeyFile)) {
    if ($null -ne $config.providers) {
      foreach ($provider in @("p8-agent", "p8-agent-fast")) {
        if ($config.providers.PSObject.Properties.Name -contains $provider) {
          $config.providers.PSObject.Properties.Remove($provider)
          Write-Step "removed provider '$provider' (no p8 key at $p8KeyFile)"
        }
      }
    }
    $pinnedModel = $config.model
    if ($pinnedModel -is [string] -and $pinnedModel -like "p8-agent*") {
      $config.PSObject.Properties.Remove("model")
      Write-Step "removed pinned model '$pinnedModel'"
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
    (Join-Path $pluginDir "tailshare"),
    (Join-Path $pluginDir "budget-guard")
  )) {
    if (Test-IsLink $link) { Remove-LinkOrFolder $link "installer link" }
  }

  foreach ($skillsDir in @((Join-Path $configDir "skills"), (Join-Path $UserHome ".agents/skills"))) {
    if (-not (Test-Path -LiteralPath $skillsDir)) { continue }
    foreach ($skill in Get-ChildItem -LiteralPath $skillsDir -Force -ErrorAction SilentlyContinue) {
      if (-not (Test-IsLink $skill.FullName)) { continue }
      $target = ($skill.Target -join " ")
      if ($target -like "*agent-kit*") {
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
