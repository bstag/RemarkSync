# Run in an interactive terminal. SSH itself prompts for the password; this
# script never reads, stores, or passes it. Existing known-host trust is required.
#
# Set REMARKSYNC_SSH_HOST to your own user@host before running; nothing about a
# particular deployment is stored here. REMARKSYNC_SSH_KEY overrides the key path.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskHost = $env:REMARKSYNC_SSH_HOST
if (-not $taskHost) { throw 'Set REMARKSYNC_SSH_HOST to the deployment user@host.' }
$taskKey = if ($env:REMARKSYNC_SSH_KEY) { $env:REMARKSYNC_SSH_KEY } else { Join-Path $taskRoot '.tools/deploy-ssh/id_ed25519' }
if (-not (Test-Path -LiteralPath $taskKey)) { throw 'The dedicated deployment key is missing.' }
$taskPublicKey = (Get-Content -LiteralPath ($taskKey + '.pub') -Raw).Trim()
if ($taskPublicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(\s\S+)?$') {
    throw 'Unexpected deployment public key format.'
}
$taskRemote = @'
set -eu
test ! -L "$HOME/.ssh"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
test ! -L "$HOME/.ssh/authorized_keys"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if ! grep -qxF 'restrict KEY_PLACEHOLDER' "$HOME/.ssh/authorized_keys"; then
    printf '\n%s\n' 'restrict KEY_PLACEHOLDER' >> "$HOME/.ssh/authorized_keys"
fi
printf 'RemarkSync deployment key installed.\n'
'@
$taskRemote = $taskRemote.Replace('KEY_PLACEHOLDER', $taskPublicKey).Replace("`r", '')
& ssh -o StrictHostKeyChecking=yes -o PubkeyAuthentication=no -o PreferredAuthentications=keyboard-interactive,password $taskHost $taskRemote
if ($LASTEXITCODE -ne 0) { throw 'SSH connection/key setup did not complete.' }
& ssh -i $taskKey -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes $taskHost 'uname -m'
if ($LASTEXITCODE -ne 0) { throw 'The new deployment key could not authenticate.' }
Write-Output 'The server is ready for automated deployment checks.'
