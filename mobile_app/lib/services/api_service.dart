import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/product.dart';
import '../models/history_item.dart';
import '../models/search_result.dart';

class ApiService extends ChangeNotifier {
  static const String _ipKey = 'tracker_host_ip';
  
  String _hostIp = '';
  bool _isConnected = false;
  bool _isConnecting = false;
  
  Map<String, dynamic> _healthStatus = {};
  List<Product> _blinkitProducts = [];
  List<Product> _zeptoProducts = [];
  List<HistoryItem> _historyLogs = [];
  
  // Daemons running status
  Map<String, bool> _daemonsRunning = {'blinkit': false, 'zepto': false};
  final Map<String, String> _daemonsLastRun = {'blinkit': '', 'zepto': ''};

  String get hostIp => _hostIp;
  bool get isConnected => _isConnected;
  bool get isConnecting => _isConnecting;
  
  Map<String, dynamic> get healthStatus => _healthStatus;
  List<Product> get blinkitProducts => _blinkitProducts;
  List<Product> get zeptoProducts => _zeptoProducts;
  List<HistoryItem> get historyLogs => _historyLogs;
  
  Map<String, bool> get daemonsRunning => _daemonsRunning;
  Map<String, String> get daemonsLastRun => _daemonsLastRun;

  String get baseUrl {
    if (_hostIp.isEmpty) return '';
    // Format appropriately: ensure http:// and no trailing slash
    String ip = _hostIp.trim();
    if (!ip.startsWith('http://') && !ip.startsWith('https://')) {
      ip = 'http://$ip';
    }
    if (ip.endsWith('/')) {
      ip = ip.substring(0, ip.length - 1);
    }
    // Default to port 8000 if no port specified
    final uri = Uri.parse(ip);
    if (!uri.hasPort) {
      ip = '$ip:8000';
    }
    return ip;
  }

  // Load saved IP and auto-check connection
  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    _hostIp = prefs.getString(_ipKey) ?? '';
    if (_hostIp.isNotEmpty) {
      await connect(_hostIp);
    }
  }

  // Connect to specified IP and check health
  Future<bool> connect(String ip) async {
    _isConnecting = true;
    notifyListeners();
    
    _hostIp = ip;
    final url = '$baseUrl/health';
    
    try {
      final response = await http.get(Uri.parse(url)).timeout(const Duration(seconds: 5));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _healthStatus = data;
        _isConnected = true;
        
        // Save successfully connected IP
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString(_ipKey, _hostIp);
        
        // Load initial dashboard details
        await refreshDashboard();
      } else {
        _isConnected = false;
      }
    } catch (_) {
      _isConnected = false;
    } finally {
      _isConnecting = false;
      notifyListeners();
    }
    return _isConnected;
  }

  // Clear connection details and log out
  Future<void> disconnect() async {
    _hostIp = '';
    _isConnected = false;
    _healthStatus = {};
    _blinkitProducts = [];
    _zeptoProducts = [];
    _historyLogs = [];
    _daemonsRunning = {'blinkit': false, 'zepto': false};
    
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_ipKey);
    notifyListeners();
  }

  // General dashboard stats update
  Future<void> refreshDashboard() async {
    if (!_isConnected) return;
    
    await Future.wait([
      _fetchDaemonStatus('blinkit'),
      _fetchDaemonStatus('zepto'),
      fetchProducts('blinkit'),
      fetchProducts('zepto'),
      fetchHistoryLogs(),
    ]);
    notifyListeners();
  }

  // Retrieve daemon tracker status
  Future<void> _fetchDaemonStatus(String provider) async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/tracker/$provider/status'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _daemonsRunning[provider] = data['is_running'] ?? false;
        _daemonsLastRun[provider] = data['last_run_time'] ?? '';
      }
    } catch (_) {}
  }

  // Retrieve configured tracked products
  Future<void> fetchProducts(String provider) async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/products/$provider'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final List productsJson = data['products'] ?? [];
        final list = productsJson.map((x) => Product.fromJson(x)).toList();
        if (provider == 'blinkit') {
          _blinkitProducts = list;
        } else {
          _zeptoProducts = list;
        }
      }
    } catch (_) {}
  }

  // Retrieve history status change logs
  Future<void> fetchHistoryLogs() async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/history'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final List historyJson = data['history'] ?? [];
        _historyLogs = historyJson.map((x) => HistoryItem.fromJson(x)).toList();
      }
    } catch (_) {}
  }

  // Toggle background daemon thread
  Future<bool> toggleDaemon(String provider, bool start) async {
    final endpoint = start ? 'start' : 'stop';
    try {
      final response = await http.post(Uri.parse('$baseUrl/tracker/$provider/$endpoint'));
      if (response.statusCode == 200) {
        await _fetchDaemonStatus(provider);
        notifyListeners();
        return true;
      }
    } catch (_) {}
    return false;
  }

  // Start all background daemons
  Future<bool> startAllTrackers() async {
    try {
      final response = await http.post(Uri.parse('$baseUrl/tracker/all/start'));
      if (response.statusCode == 200) {
        await refreshDashboard();
        return true;
      }
    } catch (_) {}
    return false;
  }

  // Stop all background daemons
  Future<bool> stopAllTrackers() async {
    try {
      final response = await http.post(Uri.parse('$baseUrl/tracker/all/stop'));
      if (response.statusCode == 200) {
        await refreshDashboard();
        return true;
      }
    } catch (_) {}
    return false;
  }

  // Add a product to tracking
  Future<bool> addProduct(String provider, String name, String url) async {
    try {
      final response = await http.post(
        Uri.parse('$baseUrl/products/$provider'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'name': name,
          'url': url,
          'status': 'UNKNOWN',
        }),
      );
      if (response.statusCode == 200) {
        await fetchProducts(provider);
        notifyListeners();
        return true;
      }
    } catch (_) {}
    return false;
  }

  // Delete a product from tracking
  Future<bool> removeProduct(String provider, String url) async {
    try {
      final encodedUrl = Uri.encodeComponent(url);
      final response = await http.delete(Uri.parse('$baseUrl/products/$provider?url=$encodedUrl'));
      if (response.statusCode == 200) {
        await fetchProducts(provider);
        notifyListeners();
        return true;
      }
    } catch (_) {}
    return false;
  }

  // Force/trigger an instant stock recheck
  Future<String> checkProductStockInstantly(String provider, String url) async {
    try {
      final encodedUrl = Uri.encodeComponent(url);
      final response = await http.get(Uri.parse('$baseUrl/stock/$provider?url=$encodedUrl'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final String newStatus = data['status'] ?? 'UNKNOWN';
        await fetchProducts(provider);
        await fetchHistoryLogs();
        notifyListeners();
        return newStatus;
      }
    } catch (_) {}
    return 'FAILED';
  }

  // Query products on the live website
  Future<List<SearchResult>> searchProducts(String provider, String query) async {
    try {
      final encodedQ = Uri.encodeComponent(query);
      final response = await http.get(Uri.parse('$baseUrl/search/$provider?q=$encodedQ'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final List resultsJson = data['results'] ?? [];
        return resultsJson.map((x) => SearchResult.fromJson(x)).toList();
      }
    } catch (_) {}
    return [];
  }

  // Get configured background keywords
  Future<List<String>> getKeywords(String provider) async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/keywords/$provider'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final List kList = data['keywords'] ?? [];
        return kList.cast<String>();
      }
    } catch (_) {}
    return [];
  }

  // Save/overwrite background keywords config
  Future<bool> saveKeywords(String provider, List<String> keywords) async {
    try {
      final response = await http.post(
        Uri.parse('$baseUrl/keywords/$provider'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'keywords': keywords}),
      );
      if (response.statusCode == 200) {
        return true;
      }
    } catch (_) {}
    return false;
  }

  // Setup location pinpoint (starts browser on Host server PC)
  Future<bool> startLocationSetup(String provider) async {
    try {
      final response = await http.post(Uri.parse('$baseUrl/setup/$provider'));
      return response.statusCode == 200;
    } catch (_) {}
    return false;
  }

  // Save location state (closes browser and saves pin cookies)
  Future<bool> saveLocationSetup(String provider) async {
    try {
      final response = await http.post(Uri.parse('$baseUrl/setup/$provider/save'));
      return response.statusCode == 200;
    } catch (_) {}
    return false;
  }

  // Start fresh application wipe
  Future<bool> resetTracker() async {
    try {
      final response = await http.post(Uri.parse('$baseUrl/reset'));
      if (response.statusCode == 200) {
        await refreshDashboard();
        return true;
      }
    } catch (_) {}
    return false;
  }
}
