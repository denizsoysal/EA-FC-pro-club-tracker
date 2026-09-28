# DubsFC Tracker v3 — selected statistics (decoder v2 unchanged)

Decoder identifier: `community-selected-stats-v2-unofficial`.

## What the viewer exposes

| View | Statistics |
| --- | --- |
| Overview | Goals, assists, second assists, successful dribbles, tackles won, average rating |
| Shooting | Goals, shots, shots on target |
| Passing | Assists, passes made, pass completion, successful through balls |
| Dribbling | Successful dribbles (reported completions), non-skill dribble beats, skill-move beats |
| Defending | Interceptions, tackles won, standing/sliding tackles won, ball recovered (dispossessions) |

Crossing, corner and header statistics are not displayed or exported as columns.
Raw match objects remain intact, including unrecognized counters. Key passes and
opponents bypassed by a pass are not inferred or shown.

## Mapping reference (unofficial)

These reported event meanings were checked against the original developer's
published findings on 28 September 2026. They are **not independently validated
for Dubs VzeDoux, FC 27, or every match type**.

| Normalized field | Source / calculation |
| --- | --- |
| `goals`, `assists`, `rating`, `shots` | Ordinary API fields with those names |
| `passes_made`, `pass_attempts` | `passesmade`, `passattempts` (camel-case aliases accepted) |
| `second_assists` | Event 115 |
| `shots_on_target` | Event 217 |
| `forward_passes_completed`, `forward_passes_failed` | Events 30, 31 |
| `long_passes_completed`, `long_passes_failed` | Events 28, 29 |
| `through_balls_completed` | Event 152 |
| `dribbles_completed` | Event 174 |
| `dribble_beats` | Event 112 minus event 38 |
| `skill_move_beats` | Event 38 |
| `interceptions` | Event 6 |
| `standing_tackles_won`, `sliding_tackles_won` | Events 229, 230 |
| `tackles_won` | Sum of the two won-tackle components |
| `opponents_dispossessed` | Event 158 |

Primary community source:
https://www.reddit.com/r/fifaclubs/comments/1ws2mv0/pro_clubs_api_decoding_match_event_aggregate/

Second assists are not labelled key passes. The source distinguishes non-skill
beats (`112 − 38`) from skill beats (`38`); the viewer keeps them separate.
“Opponents beaten” is an action count, **not a count of distinct defenders**.
Completed dribbles must not be interpreted as successful one-on-one take-ons.
Do not combine dispossessions and tackle counts into a new total: they may overlap.

## How this implementation handles uncertainty

All four `match_event_aggregate_0` ... `_3` fields must exist as strings. At least
one must contain counters. Empty, missing, duplicate or malformed entries make
all event-derived values unavailable, but do not erase ordinary goals or passes.

Within a valid populated sparse map, a missing event ID is treated as zero. That
is an explicit, **unverified sparsity assumption**, not proof that the server
supports the event in that match. Compare the data to your own in-game screens.

When event 112 is smaller than event 38, the non-skill result stays `null` and a
warning is recorded. It is never silently clamped to zero. Unrelated counters
remain available. An assist/event-11 disagreement is also flagged.

A heading marked `*` uses a community mapping. Event-coverage columns are not
displayed. Internal parser status still distinguishes missing values from zeros.

## Aggregation

Count totals sum available values only. The `†` mark means fewer appearances had
that statistic than the player's overall appearance count. The per-match switch
divides each count by its own available-appearance count, not all appearances.
There is no per-90 estimate because reliable minutes played have not been supplied.

Average rating always averages only available ratings. It is never summed.

For the displayed pass percentage (the hidden forward/long decoding follows the same rule):

```text
100 × sum(completed passes) / sum(attempted passes)
```

Both counts must be valid for a record to contribute. The completion count must
not exceed attempts. Forward/long attempts are successes plus failures from their
respective event pair. A 0-attempt total is unavailable; 0 completions out of a
positive number of attempts is a genuine 0%. We do not average match percentages.

**Example:** 1/1 in one match and 0/9 in another produces 10%, not 50%.

The match-detail tables always show that particular match, independent of the
squad's totals/per-match toggle. Filtering the period or competition happens before
player aggregation. Last-5/last-10 select club matches, not each player's last
five/ten appearances. Player search affects the player table and its CSV, not the
club summary cards or match list.

## Reviewing a new decoder version

The previous `advanced_mapping_confirmed: true` only referred to the smaller v1
mapping set. It does not automatically approve newly added definitions. After
checking **this version** against your FC 27 screens, you can add/update:

```json
"advanced_mapping_confirmed": true,
"advanced_mapping_version": "community-selected-stats-v2-unofficial"
```

Without both matching values, the viewer keeps its provisional warning. This only
records the owner's review; it is not an official EA guarantee. No config change
is required to collect, display or export the provisional metrics.

## Exports

The main **Export all match CSV** link downloads every archived player-match row,
regardless of current UI filters. It includes selected counts, pass numerators and
denominators, individual-match percentages, `events_status`, warnings and decoder
version. Missing values export as empty cells.

**Export this view** downloads the selected category's filtered/sorted player
summary. It respects player search, period, competition and totals/per-match mode.
Every metric has an accompanying `_available_matches` column for its denominator
or coverage. It is not an export of only the rows visible in a horizontal scroll.

Both exports neutralize leading spreadsheet formula prefixes in text fields.


## v3 wording changes, not new discoveries

“Successful dribbles” uses the reported **completed** counter (174), not an
attempt counter. The exact EA event definition has not been independently
validated against your in-game screens. It must not be read as “defenders beaten”
or as the numerator of a dribble-success percentage without an attempt counter.

“Ball recovered (dispossessions)” keeps event 158, reported as **dispossessed
opponent**. It describes taking the ball from an opponent rather than being
 dispossessed yourself, but is **not a validated count of all ball recoveries**.
Interceptions, loose-ball pickups and tackle outcomes are not added to manufacture
that broader metric. The underlying normalized field remains
`opponents_dispossessed` for backward compatibility.

The source log supplied by the user proves certain event IDs are present, not
that the semantic mapping is correct. No new mapping-approval setting is applied.
