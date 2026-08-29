{
  description = "Codex load balancer and proxy for ChatGPT accounts with usage dashboard";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs =
    {
      nixpkgs,
      pyproject-nix,
      pyproject-build-systems,
      uv2nix,
      ...
    }:
    let
      inherit (nixpkgs) lib;

      supportedSystems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forAllSystems = lib.genAttrs supportedSystems;

      projectMetadata = (lib.importTOML ./pyproject.toml).project;

      codexLbMeta = {
        inherit (projectMetadata) description;
        homepage = "https://github.com/Soju06/codex-lb";
        license = lib.licenses.mit;
        mainProgram = "codex-lb";
        maintainers = [ lib.maintainers.aaravrav ];
      };

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      lockedOverlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
      };

      packageSource = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [
          ./app
          ./config
          ./LICENSE
          ./README.md
          ./pyproject.toml
        ];
      };

      editableSource = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [
          ./app/__init__.py
          ./config
          ./LICENSE
          ./README.md
          ./pyproject.toml
        ];
      };

      frontendSource = lib.fileset.toSource {
        root = ./frontend;
        fileset = lib.fileset.unions [
          ./frontend/bun.lock
          ./frontend/index.html
          ./frontend/package.json
          ./frontend/public
          ./frontend/src
          ./frontend/tsconfig.app.json
          ./frontend/tsconfig.json
          ./frontend/tsconfig.node.json
          ./frontend/vite.config.ts
        ];
      };

      frontendNodeModules = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.stdenvNoCC.mkDerivation {
          pname = "codex-lb-frontend-node-modules";
          inherit (projectMetadata) version;
          src = frontendSource;

          impureEnvVars = lib.fetchers.proxyImpureEnvVars ++ [
            "GIT_PROXY_COMMAND"
            "SOCKS_SERVER"
          ];

          nativeBuildInputs = [
            pkgs.bun
            pkgs.writableTmpDirAsHomeHook
          ];

          dontConfigure = true;
          buildPhase = ''
            runHook preBuild

            export BUN_INSTALL_CACHE_DIR="$(mktemp -d)"
            bun install \
              --cpu="*" \
              --frozen-lockfile \
              --ignore-scripts \
              --no-progress \
              --os="*"

            runHook postBuild
          '';
          installPhase = ''
            runHook preInstall

            mkdir -p "$out"
            cp -R node_modules "$out/"

            runHook postInstall
          '';

          dontFixup = true;
          outputHash = "sha256-Z3aEhLSn7xfE/7w7etkXcPw+YAwZUkru5uMqrXYDtlQ=";
          outputHashAlgo = "sha256";
          outputHashMode = "recursive";
        }
      );

      frontendAssets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.stdenvNoCC.mkDerivation {
          pname = "codex-lb-frontend";
          inherit (projectMetadata) version;
          src = frontendSource;

          nativeBuildInputs = [ pkgs.bun ];

          dontConfigure = true;
          buildPhase = ''
            runHook preBuild

            cp -R ${frontendNodeModules.${system}}/node_modules .
            chmod -R u+w node_modules
            bun node_modules/@typescript/native/bin/tsc -b
            bun node_modules/vite/bin/vite.js build --outDir dist

            runHook postBuild
          '';
          installPhase = ''
            runHook preInstall

            mkdir -p "$out"
            cp -R dist/. "$out/"

            runHook postInstall
          '';
        }
      );

      packageOverlay = system: _final: prev: {
        codex-lb = prev.codex-lb.overrideAttrs (old: {
          meta = codexLbMeta;
          src = packageSource;
          postPatch = (old.postPatch or "") + ''
            mkdir -p app/static
            cp -R ${frontendAssets.${system}}/. app/static/
          '';
        });
      };

      editableProjectOverlay = final: prev: {
        codex-lb = prev.codex-lb.overrideAttrs (old: {
          src = editableSource;
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ final.editables ];
          # Editable imports use the checkout, not packaged dashboard assets.
          postPatch = "";
        });
      };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python313; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            lockedOverlay
            (packageOverlay system)
          ]
        )
      );

      applicationPackages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system};
          inherit (pkgs.callPackages pyproject-nix.build.util { }) mkApplication;
        in
        mkApplication {
          venv = pythonSet.mkVirtualEnv "codex-lb-env" workspace.deps.default;
          package = pythonSet.codex-lb;
        }
      );
    in
    {
      packages = forAllSystems (system: {
        default = applicationPackages.${system};
        codex-lb = applicationPackages.${system};
      });

      apps = forAllSystems (
        system:
        let
          app = {
            type = "app";
            program = lib.getExe applicationPackages.${system};
          };
        in
        {
          default = app;
          codex-lb = app;
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope (
            lib.composeManyExtensions [
              editableOverlay
              editableProjectOverlay
            ]
          );
          virtualenv = pythonSet.mkVirtualEnv "codex-lb-dev-env" {
            codex-lb = [ "dev" ];
          };
        in
        {
          default = pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.git
              pkgs.uv
            ];

            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };

            shellHook = ''
              unset PYTHONPATH
              if repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
                export REPO_ROOT="$repo_root"
              else
                export REPO_ROOT="$PWD"
              fi
              unset repo_root
            '';
          };
        }
      );

      checks = forAllSystems (system: {
        default = applicationPackages.${system};
      });

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
