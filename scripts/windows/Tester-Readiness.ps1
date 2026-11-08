# Shared by the user launcher; health itself stays degraded without a provider.
function Get-TesterOperationalReadiness {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][uri]$Url)
    if ($Url.Scheme -cne 'http' -or $Url.Host -cne '127.0.0.1' -or
        $Url.AbsolutePath -cne '/api/health' -or $Url.Query -or $Url.UserInfo) {
        throw 'Only the configured loopback health endpoint is allowed'
    }
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $false
    $request.Proxy = $null
    $request.Timeout = 2000
    $request.ReadWriteTimeout = 2000
    $response = $null
    $stream = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        try { $response = $request.GetResponse() }
        catch [Net.WebException] {
            if ($null -eq $_.Exception.Response) { throw }
            $response = $_.Exception.Response
        }
        $code = [int]$response.StatusCode
        $receipt = [pscustomobject]@{ operational=$false; http_status=$code; health=$null; provider=$null }
        if ($code -notin @(200,503) -or $response.ContentLength -gt 65536) { return $receipt }
        $stream = $response.GetResponseStream()
        $buffer = New-Object byte[] 65537
        $length = 0
        try {
            while ($length -lt $buffer.Length) {
                $remaining = 2000 - [int]$clock.ElapsedMilliseconds
                if ($remaining -le 0) { return $receipt }
                if ($stream.CanTimeout) { $stream.ReadTimeout = $remaining }
                $count = $stream.Read($buffer,$length,$buffer.Length-$length)
                if ($count -eq 0) { break }
                $length += $count
            }
        } catch { return $receipt }
        if ($length -eq 0 -or $length -gt 65536 -or $clock.ElapsedMilliseconds -ge 2000) { return $receipt }
        try { $body = ([Text.UTF8Encoding]::new($false,$true).GetString($buffer,0,$length)) | ConvertFrom-Json -ErrorAction Stop }
        catch { return $receipt }
        if ($body.schema_version -isnot [string] -or $body.schema_version -cne '1.0' -or
            $body.data -isnot [pscustomobject]) { return $receipt }
        foreach ($name in @('database','worker','storage','status','provider')) {
            if ($body.data.$name -isnot [string]) { return $receipt }
        }
        if ($body.data.database -cne 'ok' -or $body.data.worker -cne 'ok' -or
            $body.data.storage -cne 'ok') { return $receipt }
        $receipt.operational = ($code -eq 200 -and $body.data.status -ceq 'ok' -and
            $body.data.provider -ceq 'configured') -or
            ($code -eq 503 -and $body.data.status -ceq 'degraded' -and
            $body.data.provider -ceq 'unavailable')
        $receipt.health = $body.data.status
        $receipt.provider = $body.data.provider
        return $receipt
    } finally {
        $request.Abort()
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}
