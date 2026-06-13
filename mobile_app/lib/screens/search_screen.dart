import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:provider/provider.dart';
import '../services/api_service.dart';
import '../models/search_result.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final TextEditingController _searchController = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  String _selectedProvider = 'blinkit';
  List<SearchResult> _searchResults = [];
  bool _isLoading = false;
  String? _errorMessage;

  @override
  void dispose() {
    _searchController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  Future<void> _handleSearch() async {
    final query = _searchController.text.trim();
    if (query.isEmpty) return;

    _focusNode.unfocus();
    setState(() {
      _isLoading = true;
      _errorMessage = null;
      _searchResults = [];
    });

    final apiService = Provider.of<ApiService>(context, listen: false);
    try {
      final results = await apiService.searchProducts(_selectedProvider, query);
      setState(() {
        _searchResults = results;
        if (results.isEmpty) {
          _errorMessage = 'No products found. Make sure location setup is completed and address is set.';
        }
      });
    } catch (e) {
      setState(() {
        _errorMessage = 'An error occurred during search. Check server logs.';
      });
    } finally {
      setState(() {
        _isLoading = false;
      });
    }
  }

  Future<void> _trackProduct(ApiService api, SearchResult item) async {
    final success = await api.addProduct(_selectedProvider, item.name, item.url);
    if (success && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Added "${item.name}" to track list!'),
          backgroundColor: const Color(0xFF10B981),
        ),
      );
      // Trigger update of lists
      api.refreshDashboard();
    } else if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Failed to add product to config.'),
          backgroundColor: Color(0xFFE11D48),
        ),
      );
    }
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
          'Discovery Wizard',
          style: GoogleFonts.outfit(
            fontSize: 22,
            fontWeight: FontWeight.bold,
            color: const Color(0xFF1E293B),
          ),
        ),
      ),
      body: Column(
        children: [
          // Search & Store Selector Panel
          Container(
            color: Colors.white,
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            child: Column(
              children: [
                // Store Selector (Blinkit vs Zepto)
                Row(
                  children: [
                    _buildProviderButton('blinkit', 'Blinkit', const Color(0xFFFBBF24)),
                    const SizedBox(width: 12),
                    _buildProviderButton('zepto', 'Zepto', const Color(0xFFEC4899)),
                  ],
                ),
                const SizedBox(height: 16),

                // Search Bar Input
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _searchController,
                        focusNode: _focusNode,
                        onSubmitted: (_) => _handleSearch(),
                        style: GoogleFonts.inter(fontSize: 14, color: const Color(0xFF1E293B)),
                        decoration: InputDecoration(
                          prefixIcon: const Icon(Icons.search_outlined, color: Color(0xFF6C63FF)),
                          hintText: 'Search for milk, toys, hot wheels...',
                          hintStyle: GoogleFonts.inter(color: const Color(0xFF94A3B8)),
                          filled: true,
                          fillColor: const Color(0xFFF8F9FA),
                          contentPadding: const EdgeInsets.symmetric(vertical: 14),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: BorderSide.none,
                          ),
                          focusedBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(color: Color(0xFF6C63FF), width: 1.5),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    // Search Submit Icon
                    GestureDetector(
                      onTap: _isLoading ? null : _handleSearch,
                      child: Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: const Color(0xFF6C63FF),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: const Icon(Icons.arrow_forward, color: Colors.white, size: 22),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),

          // Search Results View
          Expanded(
            child: _isLoading
                ? const Center(
                    child: CircularProgressIndicator(
                      valueColor: AlwaysStoppedAnimation<Color>(Color(0xFF6C63FF)),
                    ),
                  )
                : _errorMessage != null
                    ? SingleChildScrollView(
                        padding: const EdgeInsets.all(32),
                        child: Column(
                          children: [
                            const Icon(Icons.info_outline, color: Color(0xFF94A3B8), size: 48),
                            const SizedBox(height: 16),
                            Text(
                              _errorMessage!,
                              textAlign: TextAlign.center,
                              style: GoogleFonts.inter(
                                fontSize: 13,
                                color: const Color(0xFF64748B),
                                height: 1.6,
                              ),
                            ),
                          ],
                        ),
                      )
                    : _searchResults.isEmpty
                        ? Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const Icon(Icons.explore_outlined, color: Color(0xFFCBD5E1), size: 54),
                                const SizedBox(height: 12),
                                Text(
                                  'Find and track products instantly',
                                  style: GoogleFonts.outfit(
                                    fontSize: 15,
                                    fontWeight: FontWeight.bold,
                                    color: const Color(0xFF475569),
                                  ),
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  'Select a store, type a query, and explore stock status',
                                  style: GoogleFonts.inter(fontSize: 12, color: const Color(0xFF94A3B8)),
                                ),
                              ],
                            ),
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.all(16),
                            itemCount: _searchResults.length,
                            itemBuilder: (context, index) {
                              final item = _searchResults[index];
                              
                              // Check if already tracked
                              final isBlinkit = _selectedProvider == 'blinkit';
                              final trackedList = isBlinkit ? apiService.blinkitProducts : apiService.zeptoProducts;
                              final isAlreadyTracked = trackedList.any((p) => p.url == item.url);
                              final isInstock = item.status == 'IN_STOCK';

                              return Container(
                                margin: const EdgeInsets.symmetric(vertical: 6),
                                decoration: BoxDecoration(
                                  color: Colors.white,
                                  borderRadius: BorderRadius.circular(16),
                                  boxShadow: [
                                    BoxShadow(
                                      color: Colors.black.withValues(alpha: 0.01),
                                      blurRadius: 10,
                                      offset: const Offset(0, 4),
                                    )
                                  ],
                                ),
                                padding: const EdgeInsets.all(16),
                                child: Row(
                                  children: [
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            item.name,
                                            maxLines: 2,
                                            overflow: TextOverflow.ellipsis,
                                            style: GoogleFonts.inter(
                                              fontSize: 14,
                                              fontWeight: FontWeight.w600,
                                              color: const Color(0xFF1E293B),
                                            ),
                                          ),
                                          const SizedBox(height: 4),
                                          if (item.details.isNotEmpty) ...[
                                            Text(
                                              item.details,
                                              style: GoogleFonts.inter(
                                                fontSize: 11,
                                                color: const Color(0xFF64748B),
                                              ),
                                            ),
                                            const SizedBox(height: 6),
                                          ],
                                          // Status Badge
                                          Container(
                                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                            decoration: BoxDecoration(
                                              color: isInstock 
                                                ? const Color(0xFFECFDF5) 
                                                : const Color(0xFFFFF1F2),
                                              borderRadius: BorderRadius.circular(8),
                                            ),
                                            child: Text(
                                              item.status,
                                              style: GoogleFonts.inter(
                                                fontSize: 9,
                                                fontWeight: FontWeight.bold,
                                                color: isInstock 
                                                  ? const Color(0xFF059669) 
                                                  : const Color(0xFFE11D48),
                                              ),
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                    const SizedBox(width: 12),
                                    
                                    // Track Button
                                    isAlreadyTracked
                                        ? Container(
                                            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                            decoration: BoxDecoration(
                                              color: const Color(0xFFF1F5F9),
                                              borderRadius: BorderRadius.circular(10),
                                            ),
                                            child: Text(
                                              'Tracked',
                                              style: GoogleFonts.inter(
                                                fontSize: 12,
                                                fontWeight: FontWeight.bold,
                                                color: const Color(0xFF94A3B8),
                                              ),
                                            ),
                                          )
                                        : ElevatedButton.icon(
                                            onPressed: () => _trackProduct(apiService, item),
                                            icon: const Icon(Icons.add, size: 14),
                                            label: Text(
                                              'Track',
                                              style: GoogleFonts.inter(
                                                fontSize: 12,
                                                fontWeight: FontWeight.bold,
                                              ),
                                            ),
                                            style: ElevatedButton.styleFrom(
                                              backgroundColor: const Color(0xFF6C63FF),
                                              foregroundColor: Colors.white,
                                              elevation: 0,
                                              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                                              shape: RoundedRectangleBorder(
                                                borderRadius: BorderRadius.circular(10),
                                              ),
                                            ),
                                          ),
                                  ],
                                ),
                              );
                            },
                          ),
          ),
        ],
      ),
    );
  }

  // Component structure for store selector options
  Widget _buildProviderButton(String id, String label, Color accentColor) {
    final isSelected = _selectedProvider == id;
    return Expanded(
      child: GestureDetector(
        onTap: () {
          setState(() {
            _selectedProvider = id;
          });
        },
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            color: isSelected ? accentColor.withValues(alpha: 0.12) : const Color(0xFFF8F9FA),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: isSelected ? accentColor : const Color(0xFFE2E8F0),
              width: 1.5,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                isSelected ? Icons.radio_button_checked : Icons.radio_button_off,
                size: 16,
                color: isSelected ? accentColor : const Color(0xFF94A3B8),
              ),
              const SizedBox(width: 8),
              Text(
                label,
                style: GoogleFonts.outfit(
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  color: isSelected ? accentColor : const Color(0xFF64748B),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
