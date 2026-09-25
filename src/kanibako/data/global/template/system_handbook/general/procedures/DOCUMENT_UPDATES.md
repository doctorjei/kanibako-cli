# Document Sweep

The document sweep is typically used at the end of a session to ensure that critical information
from a session is stored before context is cleared. As you will lose all unstored data when context
is cleared, it is critical that such information is not lost.

## What to Check

The sweep should include evaluation of all of the following, unless otherwise instructed:

 - Documentation - user, agent, and developer-oriented, and other reasonably expected audiences
 - Memory - includes compaction as necessary and appropriate
 - Development State - tasks and devnotes (see STATE_CLEANUP, and also ensure items are recorded)
 - Relevant lists tracking information for design and/or implementation, if applicable
 - Plans - Ideation, design, implementation, and/or other topics, as applicable
 - Designs - Specifications, conventions, procedures, and any other design documentation
 - Status - Anything tracking the current project status.
 - Any and all other relevant written information that is part of the project

## Things to Remember
 - Do the sweep yourself; do not assign it to subagents, who lack critical context to determine relevance.
 - Be thorough; anything missed is likely gone forever after context is cleared, so don't cut corners.
 - Shipped code owes shipped docs, in the same commit if it changes behavior non-trivially.
 - Sweep for information that surfaces only in conversation - decisions, rulings, and future promises.
   Fresh agents won't have it, so you have to capture it.

## Sweep Steps

1. Set the "clean" marker to "false".
2. Carefully review elements in the "What to Check" list across the project corpus & update them.
3. If there were edits, issues found, or other problems, set the "clean" marker to "false".
   Otherwise,
    - If the "clean" marker is "true", two clean sweeps have been completed; terminate the procedure.
    - Else, set the "clean" marker to "true".
4. Go to step 2.

