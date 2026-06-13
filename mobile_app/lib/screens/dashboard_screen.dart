import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:provider/provider.dart';
import 'package:intl/intl.dart';
import '../services/api_service.dart';
import '../models/history_item.dart';

class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key});

  // Helper method to display date/time nicely
  String _formatDateTime(String isoString) {
    if (isoString.isEmpty) return 'Never';
    try {
      final date = DateTime.parse(isoString);
      return DateFormat('hh:mm a, dd MMM').format(date);
    } catch (_) {
      return isoString;
    }
  }

  // Trigger setup location alerts
  void _showLocationSetupDialog(BuildContext context, ApiService apiService, String provider) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: Text(
          'Launch Location Setup?',
          style: GoogleFonts.outfit(fontWeight: FontWeight.bold),
        ),
        content: Text(
          'This will open a headed browser window ON YOUR COMPUTER running the FastAPI service. '
          'Please pin your address there, then tap "Save & Save State Context" in the mobile app once finished.',
          style: GoogleFonts.inter(fontSize: 14, color: const Color(0xFF475569)),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text('Cancel', style: GoogleFonts.inter(color: const Color(0xFF64748B))),
          ),
          ElevatedButton(
            onPressed: () async {
              Navigator.pop(ctx);
              final success = await apiService.startLocationSetup(provider);
              if (success && context.mounted) {
                _showLocationSaveDialog(context, apiService, provider);
              } else if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Failed to launch setup browser for $provider')),
                );
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF6C63FF),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
            child: Text('Launch', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  // Save location state confirmation dialog
  void _showLocationSaveDialog(BuildContext context, ApiService apiService, String provider) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: Text(
          'Setup Browser Active',
          style: GoogleFonts.outfit(fontWeight: FontWeight.bold),
        ),
        content: Text(
          'Ensure you have selected and loaded your delivery pin address in the opened browser window on your computer. '
          'Click Save below to store the context cookies and close the session.',
          style: GoogleFonts.inter(fontSize: 14, color: const Color(0xFF475569)),
        ),
        actions: [
          ElevatedButton(
            onPressed: () async {
              final success = await apiService.saveLocationSetup(provider);
              if (ctx.mounted) Navigator.pop(ctx);
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text(success 
                      ? 'Successfully saved location context for $provider!' 
                      : 'Failed to finalize location context save.'
                    ),
                  ),
                );
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF10B981),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
            child: Text('Save Location Context', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  // Fresh Reset confirmation popup
  void _showResetDialog(BuildContext context, ApiService apiService) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: Text(
          'Wipe & Start Fresh?',
          style: GoogleFonts.outfit(fontWeight: FontWeight.bold, color: const Color(0xFFE11D48)),
        ),
        content: Text(
          'This will stop background trackers, delete local history, clear location states, and reset configs to defaults. '
          'This action cannot be undone.',
          style: GoogleFonts.inter(fontSize: 14, color: const Color(0xFF475569)),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text('Cancel', style: GoogleFonts.inter(color: const Color(0xFF64748B))),
          ),
          ElevatedButton(
            onPressed: () async {
              Navigator.pop(ctx);
              final success = await apiService.resetTracker();
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text(success ? 'Application reset successfully!' : 'Wipe failed.')),
                );
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFE11D48),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
            child: Text('Reset Fresh', style: GoogleFonts.inter(fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final apiService = Provider.of<ApiService>(context);

    return Scaffold(
      backgroundColor: const Color(0xFFF8F9FA),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        title: Text(
          'Monitor Center',
          style: GoogleFonts.outfit(
            fontSize: 22,
            fontWeight: FontWeight.bold,
            color: const Color(0xFF1E293B),
          ),
        ),
        actions: [
          // Start Fresh Reset button
          IconButton(
            tooltip: 'Wipe & Reset Everything',
            icon: const Icon(Icons.refresh_outlined, color: Color(0xFFE11D48)),
            onPressed: () => _showResetDialog(context, apiService),
          ),
          // Disconnect client button
          IconButton(
            tooltip: 'Disconnect Client',
            icon: const Icon(Icons.logout_outlined, color: Color(0xFF64748B)),
            onPressed: () => apiService.disconnect(),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: apiService.refreshDashboard,
        color: const Color(0xFF6C63FF),
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(20.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Host Network Connection info
              Container(
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(16),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withOpacity(0.02),
                      blurRadius: 10,
                      offset: const Offset(0, 4),
                    )
                  ],
                ),
                padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
                child: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.all(8),
                      decoration: const BoxDecoration(
                        color: Color(0xFFEEF2F6),
                        shape: BoxShape.circle,
                      ),
                      child: const Icon(Icons.link_outlined, color: Color(0xFF6C63FF), size: 20),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Connected API Server',
                            style: GoogleFonts.inter(fontSize: 11, color: const Color(0xFF94A3B8)),
                          ),
                          Text(
                            apiService.baseUrl,
                            style: GoogleFonts.inter(
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                              color: const Color(0xFF475569),
                            ),
                          ),
                        ],
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: const Color(0xFFECFDF5),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Text(
                        'Online',
                        style: GoogleFonts.inter(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                          color: const Color(0xFF059669),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),

              // Universal Daemon controls
              Row(
                children: [
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: apiService.startAllTrackers,
                      icon: const Icon(Icons.play_arrow_outlined, size: 18),
                      label: Text('Start All', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF6C63FF),
                        foregroundColor: Colors.white,
                        elevation: 0,
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: apiService.stopAllTrackers,
                      icon: const Icon(Icons.stop_outlined, size: 18),
                      label: Text('Stop All', style: GoogleFonts.outfit(fontWeight: FontWeight.bold)),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: const Color(0xFFE11D48),
                        side: const BorderSide(color: Color(0xFFFECDD3), width: 1.5),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 24),

              // Provider Daemons section header
              Text(
                'Background Scraper Daemons',
                style: GoogleFonts.outfit(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                  color: const Color(0xFF1E293B),
                ),
              ),
              const SizedBox(height: 12),

              // Scraper Daemons status list
              _buildDaemonCard(
                context: context,
                apiService: apiService,
                provider: 'blinkit',
                title: 'Blinkit Monitor',
                subText: 'Headless detail stock & keyword searches',
                icon: Icons.flash_on_outlined,
                iconColor: const Color(0xFFFBBF24),
              ),
              const SizedBox(height: 12),
              _buildDaemonCard(
                context: context,
                apiService: apiService,
                provider: 'zepto',
                title: 'Zepto Monitor',
                subText: 'Headless detail stock & keyword searches',
                icon: Icons.shopping_bag_outlined,
                iconColor: const Color(0xFFEC4899),
              ),
              const SizedBox(height: 24),

              // Status history title
              Text(
                'Recent Log Feed',
                style: GoogleFonts.outfit(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                  color: const Color(0xFF1E293B),
                ),
              ),
              const SizedBox(height: 12),

              // Status history feed
              apiService.historyLogs.isEmpty
                  ? Container(
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(16),
                      ),
                      padding: const EdgeInsets.all(32.0),
                      child: Column(
                        children: [
                          const Icon(Icons.feed_outlined, color: Color(0xFFCBD5E1), size: 40),
                          const SizedBox(height: 12),
                          Text(
                            'No recent status history logs',
                            textAlign: TextAlign.center,
                            style: GoogleFonts.inter(fontSize: 13, color: const Color(0xFF64748B)),
                          ),
                        ],
                      ),
                    )
                  : ListView.separated(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      itemCount: apiService.historyLogs.length > 15 ? 15 : apiService.historyLogs.length,
                      separatorBuilder: (context, index) => const SizedBox(height: 8),
                      itemBuilder: (context, index) {
                        final HistoryItem log = apiService.historyLogs[index];
                        final isStock = log.status == 'IN_STOCK';
                        return Container(
                          decoration: BoxDecoration(
                            color: Colors.white,
                            borderRadius: BorderRadius.circular(14),
                          ),
                          padding: const EdgeInsets.all(12),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                padding: const EdgeInsets.all(8),
                                decoration: BoxDecoration(
                                  color: isStock ? const Color(0xFFECFDF5) : const Color(0xFFFFF1F2),
                                  shape: BoxShape.circle,
                                ),
                                child: Icon(
                                  isStock ? Icons.check_circle_outline : Icons.remove_circle_outline,
                                  color: isStock ? const Color(0xFF10B981) : const Color(0xFFEF4444),
                                  size: 16,
                                ),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      log.productName,
                                      style: GoogleFonts.inter(
                                        fontSize: 13,
                                        fontWeight: FontWeight.w600,
                                        color: const Color(0xFF334155),
                                      ),
                                    ),
                                    const SizedBox(height: 2),
                                    Text(
                                      _formatDateTime(log.timestamp),
                                      style: GoogleFonts.inter(fontSize: 10, color: const Color(0xFF94A3B8)),
                                    ),
                                  ],
                                ),
                              ),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                decoration: BoxDecoration(
                                  color: isStock ? const Color(0xFFD1FAE5) : const Color(0xFFFFD3D3),
                                  borderRadius: BorderRadius.circular(8),
                                ),
                                child: Text(
                                  log.status,
                                  style: GoogleFonts.inter(
                                    fontSize: 9,
                                    fontWeight: FontWeight.bold,
                                    color: isStock ? const Color(0xFF065F46) : const Color(0xFF991B1B),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
            ],
          ),
        ),
      ),
    );
  }

  // Component structure for daemon cards
  Widget _buildDaemonCard({
    required BuildContext context,
    required ApiService apiService,
    required String provider,
    required String title,
    required String subText,
    required IconData icon,
    required Color iconColor,
  }) {
    final isRunning = apiService.daemonsRunning[provider] ?? false;
    final lastRun = apiService.daemonsLastRun[provider] ?? '';

    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(18),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.01),
            blurRadius: 10,
            offset: const Offset(0, 4),
          )
        ],
      ),
      padding: const EdgeInsets.all(16.0),
      child: Column(
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: iconColor.withOpacity(0.1),
                  shape: BoxShape.circle,
                ),
                child: Icon(icon, color: iconColor, size: 22),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: GoogleFonts.outfit(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                        color: const Color(0xFF1E293B),
                      ),
                    ),
                    Text(
                      subText,
                      style: GoogleFonts.inter(fontSize: 11, color: const Color(0xFF64748B)),
                    ),
                  ],
                ),
              ),
              Switch.adaptive(
                value: isRunning,
                activeTrackColor: const Color(0xFF6C63FF),
                onChanged: (val) {
                  apiService.toggleDaemon(provider, val);
                },
              ),
            ],
          ),
          const SizedBox(height: 12),
          const Divider(height: 1, color: Color(0xFFF1F5F9)),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Last Update Run',
                    style: GoogleFonts.inter(fontSize: 10, color: const Color(0xFF94A3B8)),
                  ),
                  Text(
                    _formatDateTime(lastRun),
                    style: GoogleFonts.inter(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: const Color(0xFF475569),
                    ),
                  ),
                ],
              ),
              // Location setup trigger button
              TextButton.icon(
                onPressed: () => _showLocationSetupDialog(context, apiService, provider),
                icon: const Icon(Icons.location_on_outlined, size: 14),
                label: Text(
                  'Set Location',
                  style: GoogleFonts.inter(fontSize: 11, fontWeight: FontWeight.bold),
                ),
                style: TextButton.styleFrom(
                  foregroundColor: const Color(0xFF6C63FF),
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  backgroundColor: const Color(0xFFEEF2F6),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
