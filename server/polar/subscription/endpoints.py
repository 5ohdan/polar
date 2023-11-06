from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import UUID4

from polar.auth.dependencies import Auth, UserRequiredAuth
from polar.authz.service import Authz
from polar.enums import Platforms
from polar.exceptions import BadRequest, ResourceNotFound
from polar.kit.pagination import ListResource, PaginationParamsQuery
from polar.models import Repository, SubscriptionBenefit, SubscriptionTier
from polar.models.organization import Organization
from polar.models.subscription_benefit import SubscriptionBenefitType
from polar.models.subscription_tier import SubscriptionTierType
from polar.organization.dependencies import (
    OptionalOrganizationNamePlatform,
    OptionalOrganizationNameQuery,
    OrganizationNameQuery,
)
from polar.organization.service import organization as organization_service
from polar.postgres import AsyncSession, get_db_session
from polar.posthog import posthog
from polar.repository.dependencies import OptionalRepositoryNameQuery
from polar.repository.service import repository as repository_service
from polar.tags.api import Tags

from .dependencies import SearchSorting
from .schemas import (
    SubscribeSession,
    SubscribeSessionCreate,
    Subscription,
    SubscriptionBenefitCreate,
    SubscriptionBenefitUpdate,
    SubscriptionsSummary,
    SubscriptionTierBenefitsUpdate,
    SubscriptionTierCreate,
    SubscriptionTierUpdate,
    subscription_benefit_schema_map,
)
from .schemas import SubscriptionBenefit as SubscriptionBenefitSchema
from .schemas import SubscriptionTier as SubscriptionTierSchema
from .service.subscription import SearchSortProperty
from .service.subscription import subscription as subscription_service
from .service.subscription_benefit import (
    subscription_benefit as subscription_benefit_service,
)
from .service.subscription_tier import subscription_tier as subscription_tier_service


async def is_feature_flag_enabled(auth: UserRequiredAuth) -> None:
    if posthog.client and not posthog.client.feature_enabled(
        "subscriptions", auth.user.posthog_distinct_id
    ):
        raise HTTPException(403, "You don't have access to this feature.")


router = APIRouter(
    prefix="/subscriptions",
    tags=["subscriptions"],
    dependencies=[Depends(is_feature_flag_enabled)],
)


@router.get(
    "/tiers/search",
    response_model=ListResource[SubscriptionTierSchema],
    tags=[Tags.PUBLIC],
)
async def search_subscription_tiers(
    pagination: PaginationParamsQuery,
    organization_name: OrganizationNameQuery,
    repository_name: OptionalRepositoryNameQuery = None,
    direct_organization: bool = Query(True),
    include_archived: bool = Query(False),
    type: SubscriptionTierType | None = Query(None),
    platform: Platforms = Query(...),
    session: AsyncSession = Depends(get_db_session),
    auth: Auth = Depends(Auth.optional_user),
) -> ListResource[SubscriptionTierSchema]:
    organization = await organization_service.get_by_name(
        session, platform, organization_name
    )
    if organization is None:
        raise ResourceNotFound("Organization not found")

    repository: Repository | None = None
    if repository_name is not None:
        repository = await repository_service.get_by_org_and_name(
            session, organization.id, repository_name
        )
        if repository is None:
            raise ResourceNotFound("Repository not found")

    results, count = await subscription_tier_service.search(
        session,
        auth.subject,
        type=type,
        organization=organization,
        repository=repository,
        direct_organization=direct_organization,
        include_archived=include_archived,
        pagination=pagination,
    )

    return ListResource.from_paginated_results(
        [SubscriptionTierSchema.from_orm(result) for result in results],
        count,
        pagination,
    )


@router.get(
    "/tiers/lookup",
    response_model=SubscriptionTierSchema,
    tags=[Tags.PUBLIC],
)
async def lookup_subscription_tier(
    subscription_tier_id: UUID4,
    auth: Auth = Depends(Auth.optional_user),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionTier:
    subscription_tier = await subscription_tier_service.get_by_id(
        session, auth.subject, subscription_tier_id
    )

    if subscription_tier is None:
        raise ResourceNotFound()

    return subscription_tier


@router.post(
    "/tiers/",
    response_model=SubscriptionTierSchema,
    status_code=201,
    tags=[Tags.PUBLIC],
)
async def create_subscription_tier(
    subscription_tier_create: SubscriptionTierCreate,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionTier:
    return await subscription_tier_service.user_create(
        session, authz, subscription_tier_create, auth.user
    )


@router.post("/tiers/{id}", response_model=SubscriptionTierSchema, tags=[Tags.PUBLIC])
async def update_subscription_tier(
    id: UUID4,
    subscription_tier_update: SubscriptionTierUpdate,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionTier:
    subscription_tier = await subscription_tier_service.get_by_id(
        session, auth.subject, id
    )

    if subscription_tier is None:
        raise ResourceNotFound()

    return await subscription_tier_service.user_update(
        session, authz, subscription_tier, subscription_tier_update, auth.user
    )


@router.post(
    "/tiers/{id}/archive", response_model=SubscriptionTierSchema, tags=[Tags.PUBLIC]
)
async def archive_subscription_tier(
    id: UUID4,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionTier:
    subscription_tier = await subscription_tier_service.get_by_id(
        session, auth.subject, id
    )

    if subscription_tier is None:
        raise ResourceNotFound()

    return await subscription_tier_service.archive(
        session, authz, subscription_tier, auth.user
    )


@router.post(
    "/tiers/{id}/benefits", response_model=SubscriptionTierSchema, tags=[Tags.PUBLIC]
)
async def update_subscription_tier_benefits(
    id: UUID4,
    benefits_update: SubscriptionTierBenefitsUpdate,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionTier:
    subscription_tier = await subscription_tier_service.get_by_id(
        session, auth.subject, id
    )

    if subscription_tier is None:
        raise ResourceNotFound()

    subscription_tier, _, _ = await subscription_tier_service.update_benefits(
        session, authz, subscription_tier, benefits_update.benefits, auth.user
    )
    return subscription_tier


@router.get(
    "/benefits/search",
    response_model=ListResource[SubscriptionBenefitSchema],
    tags=[Tags.PUBLIC],
)
async def search_subscription_benefits(
    pagination: PaginationParamsQuery,
    organization_name: OrganizationNameQuery,
    auth: UserRequiredAuth,
    repository_name: OptionalRepositoryNameQuery = None,
    direct_organization: bool = Query(True),
    type: SubscriptionBenefitType | None = Query(None),
    platform: Platforms = Query(...),
    session: AsyncSession = Depends(get_db_session),
) -> ListResource[SubscriptionBenefitSchema]:
    organization = await organization_service.get_by_name(
        session, platform, organization_name
    )
    if organization is None:
        raise ResourceNotFound("Organization not found")

    repository: Repository | None = None
    if repository_name is not None:
        repository = await repository_service.get_by_org_and_name(
            session, organization.id, repository_name
        )
        if repository is None:
            raise ResourceNotFound("Repository not found")

    results, count = await subscription_benefit_service.search(
        session,
        auth.subject,
        type=type,
        organization=organization,
        repository=repository,
        direct_organization=direct_organization,
        pagination=pagination,
    )

    return ListResource.from_paginated_results(
        [
            subscription_benefit_schema_map[result.type].from_orm(result)
            for result in results
        ],
        count,
        pagination,
    )


@router.get(
    "/benefits/lookup",
    response_model=SubscriptionBenefitSchema,
    tags=[Tags.PUBLIC],
)
async def lookup_subscription_benefit(
    subscription_benefit_id: UUID4,
    auth: UserRequiredAuth,
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionBenefit:
    subscription_benefit = await subscription_benefit_service.get_by_id(
        session, auth.subject, subscription_benefit_id
    )

    if subscription_benefit is None:
        raise ResourceNotFound()

    return subscription_benefit


@router.post(
    "/benefits/",
    response_model=SubscriptionBenefitSchema,
    status_code=201,
    tags=[Tags.PUBLIC],
)
async def create_subscription_benefit(
    subscription_benefit_create: SubscriptionBenefitCreate,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionBenefit:
    return await subscription_benefit_service.user_create(
        session, authz, subscription_benefit_create, auth.user
    )


@router.post(
    "/benefits/{id}", response_model=SubscriptionBenefitSchema, tags=[Tags.PUBLIC]
)
async def update_subscription_benefit(
    id: UUID4,
    subscription_benefit_update: SubscriptionBenefitUpdate,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionBenefit:
    subscription_benefit = await subscription_benefit_service.get_by_id(
        session, auth.subject, id
    )

    if subscription_benefit is None:
        raise ResourceNotFound()

    return await subscription_benefit_service.user_update(
        session, authz, subscription_benefit, subscription_benefit_update, auth.user
    )


@router.delete("/benefits/{id}", status_code=204, tags=[Tags.PUBLIC])
async def delete_subscription_benefit(
    id: UUID4,
    auth: UserRequiredAuth,
    authz: Authz = Depends(Authz.authz),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    subscription_benefit = await subscription_benefit_service.get_by_id(
        session, auth.subject, id
    )

    if subscription_benefit is None:
        raise ResourceNotFound()

    await subscription_benefit_service.user_delete(
        session, authz, subscription_benefit, auth.user
    )


@router.post(
    "/subscribe-sessions/",
    response_model=SubscribeSession,
    status_code=201,
    tags=[Tags.PUBLIC],
)
async def create_subscribe_session(
    session_create: SubscribeSessionCreate,
    auth: Auth = Depends(Auth.optional_user),
    session: AsyncSession = Depends(get_db_session),
) -> SubscribeSession:
    subscription_tier = await subscription_tier_service.get_by_id(
        session, auth.subject, session_create.tier_id
    )

    if subscription_tier is None:
        raise ResourceNotFound()

    return await subscription_tier_service.create_subscribe_session(
        session,
        subscription_tier,
        session_create.success_url,
        auth.subject,
        auth.auth_method,
        customer_email=session_create.customer_email,
    )


@router.get(
    "/subscribe-sessions/{id}",
    response_model=SubscribeSession,
    tags=[Tags.PUBLIC],
)
async def get_subscribe_session(
    id: str,
    session: AsyncSession = Depends(get_db_session),
) -> SubscribeSession:
    return await subscription_tier_service.get_subscribe_session(session, id)


@router.get(
    "/subscriptions/summary", response_model=SubscriptionsSummary, tags=[Tags.PUBLIC]
)
async def get_subscriptions_summary(
    auth: UserRequiredAuth,
    organization_name: OrganizationNameQuery,
    repository_name: OptionalRepositoryNameQuery = None,
    platform: Platforms = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
    direct_organization: bool = Query(True),
    type: SubscriptionTierType | None = Query(None),
    subscription_tier_id: UUID4 | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionsSummary:
    organization = await organization_service.get_by_name(
        session, platform, organization_name
    )
    if organization is None:
        raise ResourceNotFound("Organization not found")

    repository: Repository | None = None
    if repository_name is not None:
        repository = await repository_service.get_by_org_and_name(
            session, organization.id, repository_name
        )
        if repository is None:
            raise ResourceNotFound("Repository not found")

    periods = await subscription_service.get_periods_summary(
        session,
        auth.user,
        start_date=start_date,
        end_date=end_date,
        organization=organization,
        repository=repository,
        direct_organization=direct_organization,
        type=type,
        subscription_tier_id=subscription_tier_id,
    )
    return SubscriptionsSummary(periods=periods)


@router.get(
    "/subscriptions/search",
    response_model=ListResource[Subscription],
    tags=[Tags.PUBLIC],
)
async def search_subscriptions(
    auth: UserRequiredAuth,
    pagination: PaginationParamsQuery,
    sorting: SearchSorting,
    organization_name_platform: OptionalOrganizationNamePlatform,
    repository_name: OptionalRepositoryNameQuery = None,
    direct_organization: bool = Query(True),
    type: SubscriptionTierType | None = Query(None),
    subscription_tier_id: UUID4 | None = Query(None),
    subscriber_user_id: UUID4 | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> ListResource[Subscription]:
    organization: Organization | None = None
    if organization_name_platform is not None:
        organization_name, platform = organization_name_platform
        organization = await organization_service.get_by_name(
            session, platform, organization_name
        )
        if organization is None:
            raise ResourceNotFound("Organization not found")

    repository: Repository | None = None
    if repository_name is not None:
        if organization is None:
            raise BadRequest(
                "organization_name and platform are required when repository_name is set"
            )
        repository = await repository_service.get_by_org_and_name(
            session, organization.id, repository_name
        )
        if repository is None:
            raise ResourceNotFound("Repository not found")

    results, count = await subscription_service.search(
        session,
        auth.user,
        type=type,
        organization=organization,
        repository=repository,
        direct_organization=direct_organization,
        subscription_tier_id=subscription_tier_id,
        subscriber_user_id=subscriber_user_id,
        pagination=pagination,
        sorting=sorting,
    )

    return ListResource.from_paginated_results(
        [Subscription.from_orm(result) for result in results],
        count,
        pagination,
    )
