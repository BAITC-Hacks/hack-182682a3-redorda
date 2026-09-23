import { createReadStream, statSync } from 'node:fs'
import { createServer, request as httpRequest } from 'node:http'
import { extname, join, normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)))
const distRoot = join(root, 'frontend', 'dist')
const staticRoot = join(root, 'backend', 'staticfiles')
const apiPort = Number(process.env.REDORDA_API_PORT || 8013)
const webPort = Number(process.env.REDORDA_WEB_PORT || 8103)

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ttf': 'font/ttf',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

function securityHeaders() {
  return {
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
  }
}

function sendFile(req, res, base, relativePath, immutable = false) {
  const resolvedBase = resolve(base)
  const candidate = resolve(resolvedBase, normalize(relativePath).replace(/^[/\\]+/, ''))
  if (candidate !== resolvedBase && !candidate.startsWith(`${resolvedBase}${sep}`)) {
    res.writeHead(403, securityHeaders()).end('Forbidden')
    return true
  }

  let info
  try {
    info = statSync(candidate)
  } catch {
    return false
  }
  if (!info.isFile()) return false

  const headers = {
    ...securityHeaders(),
    'Content-Length': info.size,
    'Content-Type': mimeTypes[extname(candidate).toLowerCase()] || 'application/octet-stream',
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
  }
  res.writeHead(200, headers)
  if (req.method === 'HEAD') res.end()
  else createReadStream(candidate).pipe(res)
  return true
}

function proxy(req, res) {
  const headers = {
    ...req.headers,
    host: req.headers.host || 'hack.1ge.kz',
    'x-forwarded-host': req.headers.host || 'hack.1ge.kz',
    'x-forwarded-proto': 'https',
  }
  const upstream = httpRequest({
    hostname: '127.0.0.1',
    port: apiPort,
    method: req.method,
    path: req.url,
    headers,
  }, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode || 502, {
      ...upstreamResponse.headers,
      ...securityHeaders(),
    })
    upstreamResponse.pipe(res)
  })
  upstream.on('error', () => {
    if (!res.headersSent) res.writeHead(502, securityHeaders())
    res.end('Upstream unavailable')
  })
  req.pipe(upstream)
}

const server = createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://localhost')
  let pathname
  try {
    pathname = decodeURIComponent(url.pathname)
  } catch {
    res.writeHead(400, securityHeaders()).end('Bad request')
    return
  }

  if (pathname === '/__health') {
    res.writeHead(200, { ...securityHeaders(), 'Content-Type': 'application/json' })
    res.end('{"status":"ok"}')
    return
  }
  if (pathname === '/api' || pathname.startsWith('/api/') || pathname === '/admin' || pathname.startsWith('/admin/')) {
    // Django sessions and CSRF now authenticate both reads and writes.
    proxy(req, res)
    return
  }
  if (pathname.startsWith('/static/')) {
    if (!sendFile(req, res, staticRoot, pathname.slice('/static/'.length), true)) {
      res.writeHead(404, securityHeaders()).end('Not found')
    }
    return
  }
  if (!['GET', 'HEAD'].includes(req.method || '')) {
    res.writeHead(405, securityHeaders()).end('Method not allowed')
    return
  }

  const assetPath = pathname === '/' ? 'index.html' : pathname.slice(1)
  if (sendFile(req, res, distRoot, assetPath, pathname.startsWith('/assets/'))) return
  if (!sendFile(req, res, distRoot, 'index.html')) {
    res.writeHead(503, securityHeaders()).end('Frontend unavailable')
  }
})

server.listen(webPort, '127.0.0.1', () => {
  process.stdout.write(`RedOrda proxy listening on 127.0.0.1:${webPort}\n`)
})
