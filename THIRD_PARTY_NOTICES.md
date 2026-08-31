# Third-party benchmark material

LoopArena includes benchmark task material derived from the pinned SCBench and
BeyondSWE releases listed in `benchmarks/upstreams.toml`. This includes task
specifications in Type II and Type III and source-task excerpts transformed
into Type I questions.

- SCBench problem material is distributed under Apache-2.0 by the pinned
  [`scb-problems`](https://github.com/gabeorlanski/scb-problems) project.
- The pinned SCBench runner is distributed under the MIT License by the
  [`SprocketLab/slop-code-bench`](https://github.com/SprocketLab/slop-code-bench)
  project. LoopArena does not redistribute that runner; users obtain it from
  the upstream repository.
- BeyondSWE benchmark material is distributed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) by the
  [`AweAI-Team/BeyondSWE-harbor`](https://huggingface.co/datasets/AweAI-Team/BeyondSWE-harbor)
  project. LoopArena selects and reformats portions of that material into its
  three benchmark views; its dataset card contains the project citation and
  attribution information.

LoopArena also includes frozen source workspaces under
`benchmarks/type2/cases/*/workspace.tar.gz`. These snapshots are benchmark
inputs, not works relicensed by LoopArena. Their original copyright notices
and licenses continue to apply.

- The SCBench workspaces were derived from the pinned `scb-problems` release.
- The BeyondSWE workspaces contain source from their respective upstream
  projects. The license or notice distributed by each project is retained in
  its workspace archive.

Complete evaluator bundles, container images, and upstream repositories are
not redistributed here. The Type II evaluator recipes contain selected test
identifiers and two small fixture patches from the pinned BeyondSWE release.
Users obtain the complete upstream material directly from the URLs pinned in
`benchmarks/upstreams.toml` under the terms published by those projects.
