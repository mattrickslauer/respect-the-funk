// Testing Library unmounts between tests only when vitest runs with `globals: true`,
// because that is how its automatic `afterEach` hook gets registered. This project
// runs with `globals: false` — an explicit import is worth more than an implicit
// global — so the hook is registered here instead.
//
// Without it, `screen` queries the whole of `document.body` including every previous
// test's render, and assertions of the form "this text must NOT be present" pass or
// fail based on test ordering. That produced a failure that looked exactly like a
// product bug: a surface appearing to render both its empty state and its error
// state at once.

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
