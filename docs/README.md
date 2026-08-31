# LoopArena project website

This directory is a dependency-free static website suitable for GitHub Pages.
It is intentionally kept in the repository alongside the public protocol and
release assets.

Preview it from the repository root:

```bash
python -m http.server 8000 --directory docs
```

Then open <http://localhost:8000/>. Do not open `index.html` directly: browsers
block the local JSON request used by the leaderboard under the `file:` scheme.

`data/results.json` contains public, aggregate v0.1.0 results only. It must not
contain private paths, credentials, provider endpoints, or raw trajectories.
The project paper is available at <https://arxiv.org/abs/2608.28281>.

No deployment workflow is enabled in this release candidate. After the public
repository is ready, GitHub Pages can publish the `docs/` directory from the
default branch without a build step.
