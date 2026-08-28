## 1. Implementation

- [x] 1.1 Bundle JetBrains Mono variable woff2 (latin, latin-ext) in public/fonts with @font-face (weight 400-500, swap, unicode-range)
- [x] 1.2 Remove the Google Fonts stylesheet + preconnects from index.html

## 2. Validation

- [x] 2.1 Built output contains no googleapis/gstatic references; fonts emitted next to Geist
- [x] 2.2 Frontend suite green; `openspec validate --specs`
