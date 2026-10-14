# Bibliotecas front-end (vendorizadas)

Servidas localmente para o app funcionar sem CDN (self-hosted, PWA, privacidade). Todas com licença MIT; o arquivo de licença de cada uma está na respectiva pasta.

| Biblioteca | Versão | Arquivos |
|---|---|---|
| Bootstrap | 5.3.0 | `bootstrap/5.3.0/bootstrap.min.css`, `bootstrap.bundle.min.js` |
| Bootstrap Icons | 1.11.0 | `bootstrap-icons/1.11.0/bootstrap-icons.min.css`, `fonts/*.woff2`, `*.woff` |
| Chart.js | 4.4.0 | `chartjs/4.4.0/chart.umd.js` |
| SortableJS | 1.15.0 | `sortablejs/1.15.0/Sortable.min.js` |

Origem: pacotes oficiais do npm (`npm pack <pacote>@<versão>`). Os comentários `sourceMappingURL` foram removidos
porque o `ManifestStaticFilesStorage` do Django falha se o `.map` referenciado não existir.

Para atualizar uma biblioteca: baixe o pacote novo, copie os mesmos arquivos para uma pasta com a nova versão,
troque as referências em `templates/` e apague a pasta antiga.
