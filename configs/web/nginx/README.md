# nginx on the web VM

The `web` VM (`2a0c:b641:b50:2::30`) runs `hyrule-web` (uvicorn) on
`[::]:8080` and nginx on `[::]:8081` for the static `as215932.net`
info site. Caddy on the `proxy` VM reverse-proxies both.

## Install

```
apt install nginx-light
```

## Deploy `as215932.net`

1. Copy the site content:

   ```
   rsync -av configs/as215932-net/html/ \
       root@[2a0c:b641:b50:2::30]:/var/www/as215932.net/
   ```

2. Drop the server block and enable it:

   ```
   scp configs/web/nginx/as215932.net.conf \
       root@[2a0c:b641:b50:2::30]:/etc/nginx/sites-available/as215932.net.conf
   ssh root@[2a0c:b641:b50:2::30] \
       'ln -sf /etc/nginx/sites-available/as215932.net.conf \
               /etc/nginx/sites-enabled/as215932.net.conf \
        && nginx -t && systemctl reload nginx'
   ```

3. Smoke-test from `proxy` (infra network):

   ```
   curl -6 -H "Host: as215932.net" http://[2a0c:b641:b50:2::30]:8081/ | head
   curl -6 -sI -H "Host: as215932.net" \
       http://[2a0c:b641:b50:2::30]:8081/peering
   ```
