# Third-party material

Task descriptions and selected adaptation files derive from SCBench, BeyondSWE, and LoopsBench. Exact source revisions and download locations are recorded in [benchmarks/upstreams.toml](benchmarks/upstreams.toml). Original licenses and copyright notices remain applicable; this repository's Apache-2.0 license covers its own code only.

- [SCBench problems](https://github.com/gabeorlanski/scb-problems): Apache-2.0. Its separate [runner](https://github.com/SprocketLab/slop-code-bench) is MIT-licensed and downloaded by the user.
- [BeyondSWE-harbor](https://huggingface.co/datasets/AweAI-Team/BeyondSWE-harbor): CC BY 4.0 benchmark material. Selected tasks are reformatted into the three benchmark views. Source code from individual target projects retains each project's own license and notices.
- [LoopsBench](https://github.com/microsoft/Loopsbench) and its [task dataset](https://huggingface.co/datasets/LoopsBench/LoopsBench): the upstream benchmark repository is MIT-licensed. Individual bundled task projects retain their respective licenses. The included small evaluator adaptations are documented by the per-task recipes.

Type II reconstruction recipes and small overlays replace complete workspace archives. They preserve the required source contents and notices without bundling full upstream repositories. Complete evaluator bundles, container images, and third-party datasets are downloaded separately. Users must retain upstream attribution and comply with the terms of each source project when using or redistributing reconstructed material.

The LoopsBench MIT license notice is reproduced below for material derived from
its benchmark repository:

MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
