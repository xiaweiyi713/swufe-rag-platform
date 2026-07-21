# TLS certificate mount

Production deployment places `fullchain.pem` and `privkey.pem` in this directory.
Both files are ignored by Git. Generate or renew them through the documented
Let's Encrypt workflow in `deploy/README.md`; never use a committed test key.
