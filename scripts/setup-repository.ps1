[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$Repository,

    [string]$Owner,

    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\repository-settings.json')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-GhJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & gh @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gh failed: gh $($Arguments -join ' ')"
    }

    if ([string]::IsNullOrWhiteSpace(($output -join "`n"))) {
        return $null
    }

    return ($output -join "`n") | ConvertFrom-Json
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required.'
}

& gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Authenticate first: gh auth login'
}

if ([string]::IsNullOrWhiteSpace($Owner)) {
    $Owner = & gh api user --jq '.login'
}

$resolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -LiteralPath $resolvedConfigPath -Raw | ConvertFrom-Json
$fullName = "$Owner/$Repository"

Write-Host "[1/6] Ensuring public repository $fullName exists..."
& gh repo view $fullName --json nameWithOwner 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    & gh repo create $fullName --public --add-readme --description $config.repository.description
    if ($LASTEXITCODE -ne 0) { throw "Could not create $fullName" }
}

Write-Host '[2/6] Applying repository settings...'
$repositorySettings = $config.repository
$hasIssues = $repositorySettings.has_issues.ToString().ToLowerInvariant()
$hasProjects = $repositorySettings.has_projects.ToString().ToLowerInvariant()
$hasWiki = $repositorySettings.has_wiki.ToString().ToLowerInvariant()
$allowSquash = $repositorySettings.allow_squash_merge.ToString().ToLowerInvariant()
$allowMergeCommit = $repositorySettings.allow_merge_commit.ToString().ToLowerInvariant()
$allowRebase = $repositorySettings.allow_rebase_merge.ToString().ToLowerInvariant()
$deleteBranch = $repositorySettings.delete_branch_on_merge.ToString().ToLowerInvariant()
& gh api --method PATCH "repos/$fullName" `
    -F "description=$($repositorySettings.description)" `
    -F "homepage=$($repositorySettings.homepage)" `
    -F "visibility=$($repositorySettings.visibility)" `
    -F "has_issues=$hasIssues" `
    -F "has_projects=$hasProjects" `
    -F "has_wiki=$hasWiki" `
    -F "allow_squash_merge=$allowSquash" `
    -F "allow_merge_commit=$allowMergeCommit" `
    -F "allow_rebase_merge=$allowRebase" `
    -F "delete_branch_on_merge=$deleteBranch" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not update repository settings.' }

Write-Host '[3/6] Creating or updating the main-branch ruleset...'
$rulesetFile = New-TemporaryFile
try {
    $config.ruleset | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $rulesetFile -Encoding utf8
    $rulesets = Invoke-GhJson -Arguments @('api', "repos/$fullName/rulesets")
    $existingRuleset = @($rulesets) | Where-Object name -eq $config.ruleset.name | Select-Object -First 1

    if ($existingRuleset) {
        & gh api --method PUT "repos/$fullName/rulesets/$($existingRuleset.id)" --input $rulesetFile | Out-Null
    }
    else {
        & gh api --method POST "repos/$fullName/rulesets" --input $rulesetFile | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply the ruleset.' }
}
finally {
    Remove-Item -LiteralPath $rulesetFile -Force
}

Write-Host '[4/6] Ensuring labels and course issues exist...'
& gh label create lab --repo $fullName --color 1D76DB --description 'Course laboratory work' --force
& gh label create final-project --repo $fullName --color 6F42C1 --description 'Course final project' --force

$existingIssues = Invoke-GhJson -Arguments @(
    'issue', 'list', '--repo', $fullName, '--state', 'all', '--limit', '200', '--json', 'title,url'
)

$issueUrls = [System.Collections.Generic.List[string]]::new()
foreach ($issueDefinition in $config.issues) {
    $existingIssue = @($existingIssues) | Where-Object title -eq $issueDefinition.title | Select-Object -First 1
    if ($existingIssue) {
        $issueUrls.Add($existingIssue.url)
        continue
    }

    $body = "Tracking task for **$($issueDefinition.title)**.`n`nAcceptance criteria will be added in the corresponding lab."
    $url = & gh issue create --repo $fullName --title $issueDefinition.title --label $issueDefinition.label --body $body
    if ($LASTEXITCODE -ne 0) { throw "Could not create issue: $($issueDefinition.title)" }
    $issueUrls.Add(($url | Select-Object -Last 1))
}

Write-Host '[5/6] Ensuring the project board and workflow columns exist...'
$projectTitle = $config.project.title.Replace('{repository}', $Repository)
$projectList = Invoke-GhJson -Arguments @('project', 'list', '--owner', $Owner, '--limit', '100', '--format', 'json')
$project = @($projectList.projects) | Where-Object title -eq $projectTitle | Select-Object -First 1
if (-not $project) {
    $project = Invoke-GhJson -Arguments @(
        'project', 'create', '--owner', $Owner, '--title', $projectTitle, '--format', 'json'
    )
}

$projectNumber = [string]$project.number
$projectDetails = Invoke-GhJson -Arguments @('project', 'view', $projectNumber, '--owner', $Owner, '--format', 'json')
$projectNodeId = [string]$projectDetails.id
& gh project edit $projectNumber --owner $Owner `
    --title $projectTitle `
    --visibility PUBLIC `
    --description $config.project.description | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not publish the project.' }

$fields = Invoke-GhJson -Arguments @('project', 'field-list', $projectNumber, '--owner', $Owner, '--format', 'json')
$workflowField = @($fields.fields) | Where-Object name -eq $config.project.field | Select-Object -First 1
if (-not $workflowField) {
    & gh project field-create $projectNumber --owner $Owner `
        --name $config.project.field `
        --data-type SINGLE_SELECT `
        --single-select-options ($config.project.columns -join ',') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create project workflow field.' }
}
else {
    $optionColors = @('GRAY', 'YELLOW', 'BLUE', 'GREEN')
    $fieldOptions = for ($index = 0; $index -lt $config.project.columns.Count; $index++) {
        [ordered]@{
            name        = $config.project.columns[$index]
            color       = $optionColors[$index]
            description = "Workflow state: $($config.project.columns[$index])"
        }
    }
    $updateFieldRequest = [ordered]@{
        query = @'
mutation($input: UpdateProjectV2FieldInput!) {
  updateProjectV2Field(input: $input) {
    projectV2Field { ... on ProjectV2SingleSelectField { id } }
  }
}
'@
        variables = [ordered]@{
            input = [ordered]@{
                fieldId             = $workflowField.id
                singleSelectOptions = $fieldOptions
            }
        }
    }
    $fieldRequestFile = New-TemporaryFile
    try {
        $updateFieldRequest | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $fieldRequestFile -Encoding utf8
        & gh api graphql --input $fieldRequestFile | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not update project workflow columns.' }
    }
    finally {
        Remove-Item -LiteralPath $fieldRequestFile -Force
    }
}

$viewsQuery = @'
query($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 { views(first: 50) { nodes { name layout } } }
  }
}
'@
$viewsResponse = Invoke-GhJson -Arguments @(
    'api', 'graphql', '-f', "query=$viewsQuery", '-f', "projectId=$projectNodeId"
)
$boardView = @($viewsResponse.data.node.views.nodes) | Where-Object {
    $_.name -eq 'Board' -and $_.layout -eq 'BOARD_LAYOUT'
} | Select-Object -First 1
if (-not $boardView) {
    $createBoardMutation = @'
mutation($projectId: ID!) {
  createProjectV2View(input: {projectId: $projectId, name: "Board", layout: BOARD_LAYOUT}) {
    projectV2View { id name layout }
  }
}
'@
    & gh api graphql -f query=$createBoardMutation -f projectId=$projectNodeId | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Board view.' }
}

$repositoryNodeId = & gh api "repos/$fullName" --jq '.node_id'
$linkMutation = @'
mutation($projectId: ID!, $repositoryId: ID!) {
  linkProjectV2ToRepository(input: {projectId: $projectId, repositoryId: $repositoryId}) {
    repository { id }
  }
}
'@
& gh api graphql -f query=$linkMutation -f projectId=$projectNodeId -f repositoryId=$repositoryNodeId 2>$null | Out-Null

Write-Host '[6/6] Adding every course task to Backlog...'
$fields = Invoke-GhJson -Arguments @('project', 'field-list', $projectNumber, '--owner', $Owner, '--format', 'json')
$workflowField = @($fields.fields) | Where-Object name -eq $config.project.field | Select-Object -First 1
$backlogOption = @($workflowField.options) | Where-Object name -eq 'Backlog' | Select-Object -First 1
$items = Invoke-GhJson -Arguments @(
    'project', 'item-list', $projectNumber, '--owner', $Owner, '--limit', '500', '--format', 'json'
)

foreach ($issueUrl in $issueUrls) {
    $item = @($items.items) | Where-Object { $_.content.url -eq $issueUrl } | Select-Object -First 1
    if (-not $item) {
        $item = Invoke-GhJson -Arguments @(
            'project', 'item-add', $projectNumber, '--owner', $Owner, '--url', $issueUrl, '--format', 'json'
        )
    }

    & gh project item-edit `
        --id $item.id `
        --project-id $projectNodeId `
        --field-id $workflowField.id `
        --single-select-option-id $backlogOption.id | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not set Backlog for $issueUrl" }
}

Write-Host ''
Write-Host 'Repository configuration applied successfully.' -ForegroundColor Green
Write-Host "Repository: https://github.com/$fullName"
Write-Host "Project:    $($projectDetails.url)"
