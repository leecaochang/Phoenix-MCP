"""The ordered list of admin views the integration registers."""

from __future__ import annotations

from ..view_base import PhoenixView
from .access_keys import (
    PhoenixAdminAccessKeyConnectionView, PhoenixAdminAccessKeyPresetApplyView,
    PhoenixAdminAccessKeyPresetView, PhoenixAdminAccessKeyPresetsView,
    PhoenixAdminAccessKeyRotateView, PhoenixAdminAccessKeyStatsView, PhoenixAdminAccessKeyView,
    PhoenixAdminAccessKeysView, PhoenixAdminArchivedAccessKeyView,
    PhoenixAdminArchivedAccessKeysView,
)
from .approvals import (
    PhoenixAdminApprovalApproveView, PhoenixAdminApprovalBatchApproveView,
    PhoenixAdminApprovalRejectView, PhoenixAdminApprovalView, PhoenixAdminApprovalsView,
)
from .changes import (
    PhoenixAdminAttentionReviewView, PhoenixAdminAttentionView,
    PhoenixAdminConfigurationRecoveryRecordView, PhoenixAdminConfigurationRecoveryView,
    PhoenixAdminVersionRestoreView, PhoenixAdminVersionView, PhoenixAdminVersionsView,
)
from .logs import (
    PhoenixAdminAccessKeyAuditView, PhoenixAdminAuditView, PhoenixAdminConversationView,
    PhoenixAdminConversationsView,
)
from .mesa import (
    PhoenixAdminMesaAreaView, PhoenixAdminMesaAreasView, PhoenixAdminMesaDefaultsView,
    PhoenixAdminMesaDeviceOptionsView, PhoenixAdminMesaDeviceView, PhoenixAdminMesaDevicesView,
    PhoenixAdminMesaDomainView, PhoenixAdminMesaDomainsView, PhoenixAdminMesaExportView,
    PhoenixAdminMesaImportView, PhoenixAdminMesaIntegrationOptionsView,
    PhoenixAdminMesaIntegrationView, PhoenixAdminMesaIntegrationsView, PhoenixAdminMesaIssuesView,
    PhoenixAdminMesaOrphansClearView, PhoenixAdminMesaProfileView, PhoenixAdminMesaProfilesView,
    PhoenixAdminMesaSuggestionDismissView, PhoenixAdminMesaSuggestionRestoreView,
    PhoenixAdminMesaVocabularyView,
)
from .permissions import (
    PhoenixAdminEntityHintView, PhoenixAdminEntityHintsView, PhoenixAdminEntityTreeView,
    PhoenixAdminPermissionBulkSelectView, PhoenixAdminPermissionDeviceView,
    PhoenixAdminPermissionDomainView, PhoenixAdminPermissionEntityView,
    PhoenixAdminPermissionIntegrationOptionsView, PhoenixAdminPermissionsView,
    PhoenixAdminResolveView, PhoenixAdminScopeView,
)
from .settings import (
    PhoenixAdminAiTaskPreferredView, PhoenixAdminCardCatalogView, PhoenixAdminCatalogView,
    PhoenixAdminCommandPreviewView, PhoenixAdminInfoView, PhoenixAdminRoutingStatisticsView,
    PhoenixAdminSettingsView, PhoenixAdminVoiceAgentPipelineView,
    PhoenixAdminVoiceAgentPreferredView, PhoenixAdminWipeView,
)


ALL_ADMIN_VIEWS: list[type[PhoenixView]] = [
    PhoenixAdminInfoView,
    PhoenixAdminCatalogView,
    PhoenixAdminArchivedAccessKeysView,
    PhoenixAdminArchivedAccessKeyView,
    PhoenixAdminAccessKeysView,
    PhoenixAdminAccessKeyView,
    PhoenixAdminAccessKeyPresetsView,
    PhoenixAdminAccessKeyPresetView,
    PhoenixAdminAccessKeyPresetApplyView,
    PhoenixAdminPermissionsView,
    PhoenixAdminPermissionIntegrationOptionsView,
    PhoenixAdminPermissionBulkSelectView,
    PhoenixAdminPermissionDomainView,
    PhoenixAdminPermissionDeviceView,
    PhoenixAdminPermissionEntityView,
    PhoenixAdminResolveView,
    PhoenixAdminAccessKeyRotateView,
    PhoenixAdminScopeView,
    PhoenixAdminEntityTreeView,
    PhoenixAdminEntityHintsView,
    PhoenixAdminEntityHintView,
    PhoenixAdminAccessKeyStatsView,
    PhoenixAdminAccessKeyConnectionView,
    PhoenixAdminAccessKeyAuditView,
    PhoenixAdminAuditView,
    PhoenixAdminConversationsView,
    PhoenixAdminConversationView,
    PhoenixAdminSettingsView,
    PhoenixAdminCommandPreviewView,
    PhoenixAdminRoutingStatisticsView,
    PhoenixAdminVoiceAgentPipelineView,
    PhoenixAdminVoiceAgentPreferredView,
    PhoenixAdminAiTaskPreferredView,
    PhoenixAdminWipeView,
    PhoenixAdminApprovalsView,
    PhoenixAdminApprovalView,
    PhoenixAdminApprovalApproveView,
    PhoenixAdminApprovalBatchApproveView,
    PhoenixAdminApprovalRejectView,
    PhoenixAdminAttentionView,
    PhoenixAdminAttentionReviewView,
    PhoenixAdminConfigurationRecoveryView,
    PhoenixAdminConfigurationRecoveryRecordView,
    PhoenixAdminVersionsView,
    PhoenixAdminVersionView,
    PhoenixAdminVersionRestoreView,
    PhoenixAdminMesaProfilesView,
    PhoenixAdminMesaProfileView,
    PhoenixAdminMesaDomainsView,
    PhoenixAdminMesaDomainView,
    PhoenixAdminMesaIntegrationsView,
    PhoenixAdminMesaIntegrationView,
    PhoenixAdminMesaIntegrationOptionsView,
    PhoenixAdminMesaAreasView,
    PhoenixAdminMesaAreaView,
    PhoenixAdminMesaDevicesView,
    PhoenixAdminMesaDeviceOptionsView,
    PhoenixAdminMesaDeviceView,
    PhoenixAdminMesaVocabularyView,
    PhoenixAdminMesaDefaultsView,
    PhoenixAdminMesaIssuesView,
    PhoenixAdminMesaOrphansClearView,
    PhoenixAdminMesaExportView,
    PhoenixAdminMesaImportView,
    PhoenixAdminMesaSuggestionDismissView,
    PhoenixAdminMesaSuggestionRestoreView,
    PhoenixAdminCardCatalogView,
]
