export {
  activityReducer,
  activityStateFromSnapshot,
  compareActivityEvents,
  createActivityState,
  isActivityEventVisible,
  mergeActivityEvents,
  mergeDelegations,
  normalizeActivityEvent,
  normalizeActivityPage,
  normalizeDelegation,
  normalizeDelegationStatus,
  prependActivityPage,
  projectRailModel,
} from "./model";

export type { ActivityAction, ActivityEventInput, DelegationInput } from "./model";
