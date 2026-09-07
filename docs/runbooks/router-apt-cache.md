# Router APT binary cache policy

`rtr` has a small root filesystem. APT repeatedly regenerates approximately 93 MiB of `pkgcache.bin` and `srcpkgcache.bin`, undoing one-off cleanup. The dedicated `rtr-apt-cache` playbook disables persistent storage of these two derived indexes. Package lists, downloaded archives, installed packages, kernels, journals and Vector buffers are retained. No service restart or package transaction is required.

After green CI and review, merge and dispatch `apply.yml` from main with `playbook=rtr-apt-cache`, `limit=rtr`, first in dry-run mode and then with `dry_run=false`. Verify `apt-config dump` shows empty `Dir::Cache::pkgcache` and `Dir::Cache::srcpkgcache`, run `apt-cache gencaches`, and confirm the two binary cache files are not recreated. Check root free space and routing/logging service health afterward.

Existing binary cache files are not deleted by this playbook. The separately authorized cleanup already removed those files. This prevents that space being reused by normal APT cache generation; it does not resolve the remaining root-volume capacity constraint.

Rollback removes only `/etc/apt/apt.conf.d/99-router-binary-cache` through a reviewed change. APT then resumes its default binary-cache behavior. APT startup can be slower while persistent caches are disabled.

Reference: [Debian apt.conf(5), Directories](https://manpages.debian.org/trixie/apt/apt.conf.5.en.html#DIRECTORIES). Related: #267 and #507.
