# Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

[CmdletBinding()]
param(
    [string]$ImageName = 'my-course',
    [string]$Version = 'local',
    [ValidateSet('cpu', 'gfx110x', 'gfx1150', 'gfx1151', 'gfx1152', 'gfx120x')]
    [string]$GpuTarget = 'gfx1151',
    # Overrides base-version.txt with a full base reference or digest.
    [string]$BaseImage,
    [switch]$Push
)

$ErrorActionPreference = 'Stop'

if (-not $BaseImage) {
    $versionFile = Join-Path $PSScriptRoot 'base-version.txt'
    $values = @(Get-Content -LiteralPath $versionFile |
        ForEach-Object { ($_ -replace '#.*', '').Trim() } |
        Where-Object { $_ })
    if ($values.Count -ne 1 -or $values[0] -cnotmatch '^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$') {
        throw 'Set base-version.txt to one released Learning Cloud version (vX.Y.Z), or pass -BaseImage.'
    }
    if ($GpuTarget -eq 'cpu') {
        $BaseImage = "ghcr.io/amdresearch/auplc-default:$($values[0])"
    } else {
        $BaseImage = "ghcr.io/amdresearch/auplc-base:$($values[0])-$GpuTarget"
    }
}

$courseName = Split-Path -Leaf $PSScriptRoot
$imageRef = "${ImageName}:${Version}-${GpuTarget}"
Write-Host "Base:   $BaseImage"
Write-Host "Course: $courseName"
Write-Host "Image:  $imageRef"

# Absolute context allows invocation from any directory.
& docker build --build-arg "BASE_IMAGE=$BaseImage" --build-arg "COURSE_NAME=$courseName" `
    -t $imageRef $PSScriptRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Course image build failed; push was not attempted.'
}

if ($Push) {
    & docker push $imageRef
    if ($LASTEXITCODE -ne 0) {
        throw 'Course image push failed.'
    }
}
