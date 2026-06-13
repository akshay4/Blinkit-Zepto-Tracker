import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:provider/provider.dart';
import '../services/api_service.dart';

class KeywordsScreen extends StatefulWidget {
  const KeywordsScreen({super.key});

  @override
  State<KeywordsScreen> createState() => _KeywordsScreenState();
}

class _KeywordsScreenState extends State<KeywordsScreen> {
  final TextEditingController _keywordController = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  String _selectedProvider = 'blinkit';
  List<String> _keywords = [];
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    // Fetch initial keywords
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _loadKeywords();
    });
  }

  @override
  void dispose() {
    _keywordController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  Future<void> _loadKeywords() async {
    setState(() {
      _isLoading = true;
    });
    final apiService = Provider.of<ApiService>(context, listen: false);
    final list = await apiService.getKeywords(_selectedProvider);
    if (mounted) {
      setState(() {
        _keywords = list;
        _isLoading = false;
      });
    }
  }

  Future<void> _addKeyword() async {
    final text = _keywordController.text.trim();
    if (text.isEmpty) return;

    if (_keywords.contains(text)) {
      _keywordController.clear();
      return;
    }

    setState(() {
      _keywords.add(text);
    });
    _keywordController.clear();

    // Auto-save keywords
    final apiService = Provider.of<ApiService>(context, listen: false);
    final success = await apiService.saveKeywords(_selectedProvider, _keywords);
    if (!success && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Failed to save keywords to server')),
      );
      // Revert local state
      _loadKeywords();
    }
  }

  Future<void> _removeKeyword(String text) async {
    setState(() {
      _keywords.remove(text);
    });

    // Auto-save keywords
    final apiService = Provider.of<ApiService>(context, listen: false);
    final success = await apiService.saveKeywords(_selectedProvider, _keywords);
    if (!success && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Failed to save keywords to server')),
      );
      // Revert local state
      _loadKeywords();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8F9FA),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        title: Text(
          'Keyword Discovery',
          style: GoogleFonts.outfit(
            fontSize: 22,
            fontWeight: FontWeight.bold,
            color: const Color(0xFF1E293B),
          ),
        ),
      ),
      body: Column(
        children: [
          // Store Selector & Input Panel
          Container(
            color: Colors.white,
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
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

                // Keyword input bar
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _keywordController,
                        focusNode: _focusNode,
                        onSubmitted: (_) => _addKeyword(),
                        style: GoogleFonts.inter(fontSize: 14, color: const Color(0xFF1E293B)),
                        decoration: InputDecoration(
                          prefixIcon: const Icon(Icons.tag_outlined, color: Color(0xFF6C63FF)),
                          hintText: 'Add new discovery term (e.g. Milk)...',
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
                    GestureDetector(
                      onTap: _addKeyword,
                      child: Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: const Color(0xFF6C63FF),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: const Icon(Icons.add, color: Colors.white, size: 22),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),

          // Keywords list viewport
          Expanded(
            child: _isLoading
                ? const Center(
                    child: CircularProgressIndicator(
                      valueColor: AlwaysStoppedAnimation<Color>(Color(0xFF6C63FF)),
                    ),
                  )
                : _keywords.isEmpty
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(32.0),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              const Icon(Icons.search_off_outlined, color: Color(0xFFCBD5E1), size: 54),
                              const SizedBox(height: 12),
                              Text(
                                'No keywords configured',
                                style: GoogleFonts.outfit(
                                  fontSize: 15,
                                  fontWeight: FontWeight.bold,
                                  color: const Color(0xFF475569),
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                'Add search terms. The background daemon will alert you when new matching items appear on the platform.',
                                textAlign: TextAlign.center,
                                style: GoogleFonts.inter(fontSize: 12, color: const Color(0xFF94A3B8), height: 1.5),
                              ),
                            ],
                          ),
                        ),
                      )
                    : SingleChildScrollView(
                        padding: const EdgeInsets.all(20),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Tracked Discovery Words',
                              style: GoogleFonts.outfit(
                                fontSize: 16,
                                fontWeight: FontWeight.bold,
                                color: const Color(0xFF475569),
                              ),
                            ),
                            const SizedBox(height: 12),
                            
                            // Wrapping tag chips
                            Wrap(
                              spacing: 10,
                              runSpacing: 10,
                              children: _keywords.map((keyword) {
                                return Chip(
                                  label: Text(
                                    keyword,
                                    style: GoogleFonts.inter(
                                      fontSize: 13,
                                      fontWeight: FontWeight.w500,
                                      color: const Color(0xFF1E293B),
                                    ),
                                  ),
                                  backgroundColor: Colors.white,
                                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(10),
                                    side: const BorderSide(color: Color(0xFFE2E8F0)),
                                  ),
                                  deleteIcon: const Icon(Icons.cancel, color: Color(0xFF94A3B8), size: 18),
                                  onDeleted: () => _removeKeyword(keyword),
                                );
                              }).toList(),
                            ),
                          ],
                        ),
                      ),
          ),
        ],
      ),
    );
  }

  // Component structure for provider toggles
  Widget _buildProviderButton(String id, String label, Color accentColor) {
    final isSelected = _selectedProvider == id;
    return Expanded(
      child: GestureDetector(
        onTap: () {
          setState(() {
            _selectedProvider = id;
            _loadKeywords();
          });
        },
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            color: isSelected ? accentColor.withOpacity(0.12) : const Color(0xFFF8F9FA),
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
