# Remote Viewer HTTPS Ingress Runbook

This is an operator template for Phase 8. It does not enable remote access by itself. Configure a private VPN, a certificate for the exact hostname, and a reverse proxy on the same machine as the desktop sidecar before enabling the gateway.

## Nginx template

Replace `10.20.0.5`, `review.example.com`, `100.64.0.0/10`, and certificate paths with the VPN interface address, exact HTTPS origin, VPN client CIDR, and certificate files for the deployment. Bind Nginx to the VPN address only. The sidecar gateway remains on `127.0.0.1:8766`.

```nginx
server {
    listen 10.20.0.5:443 ssl;
    server_name review.example.com;
    if ($host != review.example.com) { return 421; }

    ssl_certificate     /etc/nginx/tls/review.example.com.crt;
    ssl_certificate_key /etc/nginx/tls/review.example.com.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    # Do not persist pairing codes, session cookies, or request headers in proxy logs.
    access_log off;
    error_log /var/log/nginx/remote-viewer-error.log crit;
    client_max_body_size 128k;

    allow 100.64.0.0/10;
    deny all;

    location = /remote {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host review.example.com;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location = /remote/ {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host review.example.com;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location ^~ /remote/assets/ {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host review.example.com;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location ^~ /remote/api/ {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host review.example.com;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location ^~ /remote/ws/ {
        proxy_pass http://127.0.0.1:8766;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host review.example.com;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 1h;
    }

    # No owner dashboard, local API, or other route is reachable through this host.
    location / {
        return 404;
    }
}
```

## Sidecar settings

Set the origin and proxy peer on the desktop host, then restart the app:

```sh
export CLINIC_ORCHESTRATOR_REMOTE_ORIGIN=https://review.example.com
export CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS=127.0.0.1
export CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT=8766
```

The environment variables must be configured for the desktop process, not only for an interactive shell that does not launch it. Do not set the local owner API bind address to a LAN/VPN interface.

## Deployment checks

1. Run `nginx -t` before reload. Confirm the certificate matches `review.example.com` and TLS is reachable only on the VPN interface.
2. Confirm listeners: Nginx is on the VPN address; the sidecar gateway is on `127.0.0.1:8766`; the owner API remains on its ephemeral loopback port.
3. From a VPN client, verify `/remote` and `/remote/assets/*` load. Verify `/api/*`, `/docs`, `/openapi.json`, and unrelated paths return 404.
4. Complete invite → owner approval → read-only task list on a second device. Confirm the second device cannot reach the owner API.
5. Revoke that device while its status WebSocket is open; verify REST access is denied and the socket closes.
6. Review Nginx and VPN logs after the test. Pairing codes, cookies, authorization headers, task diffs, and request bodies must not appear.

The template leaves the actual VPN CIDR, interface address, certificate lifecycle, firewall policy, and log retention to the operator. Phase 8 is not accepted until these values are configured and the two-device checks pass.
