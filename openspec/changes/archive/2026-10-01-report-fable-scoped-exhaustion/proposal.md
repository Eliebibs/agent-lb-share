# Change: Report Fable scoped exhaustion accurately

When every account is excluded by Anthropic's dedicated Fable weekly limit,
selection currently reports the shared base request family
`anthropic_top_thinking`. This makes healthy general five-hour and weekly usage
look inconsistent with the error and suggests that Opus thinking is also
unavailable, even though the block applies only to Fable.

Preserve routing and billing behavior, but identify the effective blocking quota
as `anthropic_fable_scoped_weekly` when all blocked candidates are hard-excluded
by that scoped limit. Mixed blocking causes continue to use the request quota.

