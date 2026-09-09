# Tool comparison: Claude Code vs Cursor

This is about the whole Day 4 branch. Routes, integration tests, Dockerfile, README, and small refactoring at places.

I used Claude Code to write a `plan.md`. Cursor did everything else with three seperate agents in their respective branches and their worktrees.

## Claude Code

Planning was the sweet spot. Day 4 has a lot of moving parts (status codes in the route, TestClient cases that match the contract, Docker/README wording). I dumped that into Claude Code and got a `plan.md` that said what goes where and in what order. That alone saved me from starting in the wrong file.

## Cursor

This is where the branch got written. I could keep the contract open, edit the handler, grow the integration tests, knock out the Dockerfile and README, and fix Swagger without rewriting how the body is parsed. For a diff this size, being inside the repo beats explaining the same context to a CLI agent over and over.

The downside is Cursor wants to code immediately. Without the plan, it kept trying to "help" in ways that would break the contract,  like binding the body as `NotificationRequest` so `/docs` looks nicer, which would throw away our 400/415 envelopes. I spent time saying "no, stay on the plan." With `plan.md` in hand that was manageable.

## Reach for which?

Claude Code when I need to think before I touch files, turn a messy lab brief into a short written sequence.

Cursor when I'm ready to land the work, several related files, contract next to code, tests and packaging in the same pass.