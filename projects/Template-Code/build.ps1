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
    [string]$ImageName = 'my-code-course',
    [string]$Version = 'local',
    [ValidateSet('cpu', 'gfx110x', 'gfx1150', 'gfx1151', 'gfx1152', 'gfx120x')]
    [string]$GpuTarget = 'gfx1151',
    # Release (vX.Y.Z) or major (vX) to use instead of the version files.
    [string]$BaseVersion,
    # Full base reference or digest to use instead of the version files.
    [string]$BaseImage,
    [switch]$Push
)

$ErrorActionPreference = 'Stop'

if (-not $BaseImage) {
    if (-not $BaseVersion) {
        $versionFile = Join-Path $PSScriptRoot 'base-version.override.txt'
        if (Test-Path -LiteralPath $versionFile) {
            Write-Warning "Using the pinned base in $versionFile"
        } else {
            # projects/course-base-major.txt sits above every course directory.
            $dir = Split-Path -Parent $PSScriptRoot
            while ($dir -and -not (Test-Path -LiteralPath (Join-Path $dir 'course-base-major.txt'))) {
                $dir = Split-Path -Parent $dir
            }
            if (-not $dir) {
                throw "course-base-major.txt not found above $PSScriptRoot; pass -BaseVersion or -BaseImage."
            }
            $versionFile = Join-Path $dir 'course-base-major.txt'
        }
        $values = @(Get-Content -LiteralPath $versionFile |
            ForEach-Object { ($_ -replace '#.*', '').Trim() } |
            Where-Object { $_ })
        $BaseVersion = if ($values.Count -eq 1) { $values[0] } else { '' }
    }
    if ($BaseVersion -cnotmatch '^v\d+(\.\d+\.\d+(-[0-9A-Za-z.]+)?)?$') {
        throw "Base version must be a major (vX) or a release (vX.Y.Z), got: $BaseVersion"
    }
    if ($GpuTarget -eq 'cpu') {
        $BaseImage = "ghcr.io/amdresearch/auplc-code-cpu:$BaseVersion"
    } else {
        $BaseImage = "ghcr.io/amdresearch/auplc-code-gpu:$BaseVersion-$GpuTarget"
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
