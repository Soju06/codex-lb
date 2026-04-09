# codex-lb Helm 안내

이 포크는 컨테이너 배포를 기준으로 유지되며, Helm/Kubernetes 배포 문서는 더 이상 적극적으로 관리하지 않습니다.

권장 배포 방식:

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/kgskr/codex-lb:latest
```

필요한 정보는 최상위 `README.md`를 기준으로 확인하면 됩니다.
