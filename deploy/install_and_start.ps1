$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[1/4] 检查 Docker Desktop / Engine 环境..." -ForegroundColor Cyan
docker version | Out-Null
docker compose version | Out-Null

Write-Host "[2/4] 检查私有化环境配置文件..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.prod.example" ".env"
    $bytes1 = New-Object byte[] 16; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes1)
    $bytes2 = New-Object byte[] 16; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes2)
    $bytes3 = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes3)
    $bytes4 = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes4)
    $pgPwd = [BitConverter]::ToString($bytes1).Replace("-", "").ToLower()
    $rtPwd = [BitConverter]::ToString($bytes2).Replace("-", "").ToLower()
    $jwtSec = [BitConverter]::ToString($bytes3).Replace("-", "").ToLower()
    $ssoSec = [BitConverter]::ToString($bytes4).Replace("-", "").ToLower()
    $raw = Get-Content ".env" -Raw
    $raw = $raw.Replace("CHANGE_ME_STRONG_PG_SUPER_PASSWORD", $pgPwd)
    $raw = $raw.Replace("CHANGE_ME_STRONG_PG_RUNTIME_PASSWORD", $rtPwd)
    $raw = $raw.Replace("CHANGE_ME_AT_LEAST_32_CHARS_RANDOM_HEX_SECRET", $jwtSec)
    $raw = $raw.Replace("CHANGE_ME_AT_LEAST_32_CHARS_PORTAL_SSO_SECRET", $ssoSec)
    Set-Content ".env" -Value $raw -Encoding UTF8
    Write-Host "已自动生成 deploy/.env 随机数据库、会话与门户单点对接密钥（如需外部大模型请补充 CYSH_LLM_API_KEY）" -ForegroundColor Green
}

Write-Host "[3/4] 构建并启动全套私有化服务容器..." -ForegroundColor Cyan
docker compose --env-file .env -f docker-compose.yml up -d --build

$portLine = Select-String -Path ".env" -Pattern "^CYSH_HTTP_PORT=(.+)$" | Select-Object -First 1
$port = if ($portLine) { $portLine.Matches[0].Groups[1].Value.Trim() } else { "18080" }

Write-Host "[4/4] 校验服务健康状态..." -ForegroundColor Cyan
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3
        Write-Host "私有化部署成功！企业用户端：http://127.0.0.1:$port/  运营管理端：http://127.0.0.1:$port/admin/" -ForegroundColor Green
        exit 0
    } catch {
        Start-Sleep -Seconds 2
    }
}
Write-Warning "服务仍在启动中，请运行 docker compose -f deploy/docker-compose.yml ps 查看详情"
